"""Boucle autonome BORNÉE : OBSERVE -> HYPOTHÈSE -> PLAN -> EXÉCUTION ->
JUDGE -> ACCEPT (promotion) / REJECT (analyse -> stratégie) -> expérience
suivante.

Bornes explicites (`LoopLimits`) : cycles, expériences par cycle et au total,
pas par expérience et au total (budget de calcul), tokens par expérience
(budget de données), modifications par expérience, délai, nouvelles
tentatives. Arrêt propre sur drapeau ou fichier `STOP`. Un seul contrôleur
à la fois par répertoire (verrou). Tout est journalisé AVANT et APRÈS chaque
étape : une expérience interrompue (crash, arrêt) est reprise au démarrage
suivant à partir de sa dernière étape consignée.

Le contrôleur n'évalue jamais lui-même un candidat : la décision vient du
Judge indépendant, la promotion du registre (qui refait la comparaison
statistique), le retour arrière du registre."""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.neural.evaluation import EvalReport, JudgeDecision
from apex.neural.loop import telemetry
from apex.neural.loop.execute import (
    StopRequested,
    execute,
    initial_model,
    load_model_from_artifact,
    measure_text,
    measure_with_details,
    reports_to_dict,
)
from apex.neural.loop.failure import (
    INTEGRITY_CAUSES,
    analyze_exception,
    analyze_judgement,
    analyze_seed_verdict,
)
from apex.neural.loop.journal import ExperimentJournal, ExperimentRecord, attest
from apex.neural.loop.judge import JUDGE_ID, IndependentJudge, JudgeResult
from apex.neural.loop.observe import Observation, observe
from apex.neural.loop.plan import (
    Budget,
    ExperimentPlan,
    InvalidPlanError,
    NonFalsifiableHypothesisError,
    Recipe,
    Weakness,
)
from apex.neural.loop.strategy import DIRECTED, RETRYABLE, StrategyDecision, StrategyPolicy
from apex.neural.loop.telemetry import Telemetry
from apex.neural.loop.world import PRIMARY, World
from apex.neural.protection import Protection, protections
from apex.neural.registry import ModelRegistry, Proposal
from apex.neural.variance import ACCEPT, INVALID, failed_integrity, pooled_sigma, seed_verdict

#: Décalage de graine de la réplique d'une promotion (graine indépendante, déterministe).
REPLICA_SEED_OFFSET = 1_000_003


class LoopBusyError(RuntimeError):
    """Un autre contrôleur tient déjà ce répertoire."""


class WorldMismatchError(RuntimeError):
    """Le journal a été produit sur un autre monde que celui fourni."""


class DuplicateExperimentError(RuntimeError):
    """Un plan identique (même empreinte) a déjà été exécuté."""


@dataclass(frozen=True)
class LoopLimits:
    max_cycles: int = 3
    max_experiments_per_cycle: int = 3
    max_total_experiments: int = 8
    max_steps_per_experiment: int = 800
    max_total_steps: int = 6000
    max_tokens_per_experiment: int = 240_000
    max_modifications: int = 1
    experiment_timeout_s: float = 900.0
    max_retries: int = 1
    checkpoint_every: int = 50
    min_weakness_gap: float = 0.1
    #: Une promotion exige qu'une RÉPLIQUE (même plan, graine indépendante) soit elle aussi
    #: acceptée par le Judge : un gain qui tient à la graine n'est pas un apprentissage.
    replicate_promotions: bool = True
    #: Protocole MULTI-GRAINES (P3.7) : toute expérience depuis un champion est entraînée sur au
    #: moins `seeds` graines indépendantes avant toute décision ; une graine de plus (jusqu'à
    #: `max_seeds`) si la variance ne permet pas de conclure. `seeds = 1` : ancien protocole
    #: (réplique seulement après un ACCEPT), choix explicite consigné au journal.
    seeds: int = 2
    max_seeds: int = 3

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool):
                continue
            if value <= 0:
                raise ValueError(f"borne « {name} » doit être strictement positive (reçu {value})")
        if self.max_seeds < self.seeds:
            raise ValueError(f"max_seeds ({self.max_seeds}) < seeds ({self.seeds})")


@contextmanager
def workdir_lock(workdir: Path) -> Iterator[None]:
    """Verrou exclusif du répertoire de boucle : un seul écrivain à la fois
    (boucle, retour arrière manuel)."""
    workdir.mkdir(parents=True, exist_ok=True)
    handle = (workdir / "loop.lock").open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LoopBusyError(f"une boucle tourne déjà dans {workdir}") from exc
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


class _LoopStop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LoopController:
    def __init__(
        self,
        workdir: Path,
        world: World,
        *,
        limits: LoopLimits | None = None,
        policy: StrategyPolicy | None = None,
        bootstrap: Recipe | None = None,
        stop: threading.Event | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.workdir = workdir
        self.world = world
        self.limits = limits or LoopLimits()
        self.policy = policy or StrategyPolicy(
            max_steps=self.limits.max_steps_per_experiment,
            max_tokens=self.limits.max_tokens_per_experiment,
            max_retries=self.limits.max_retries,
        )
        self.bootstrap = bootstrap
        self.stop = stop or threading.Event()
        self.log = log
        workdir.mkdir(parents=True, exist_ok=True)
        self.journal = ExperimentJournal(workdir / "journal.jsonl")
        self.registry = ModelRegistry(
            workdir / "registry.jsonl",
            primary=PRIMARY,
            acceptance=world.acceptance_suites,
            tolerances=world.metric_tolerances,
            protected_groups=world.protected_groups,
        )
        self.store = LocalFilesystemArtifactStore(root=str(workdir / "artifacts"))
        self.judge = IndependentJudge(world, self.store)
        self.stop_file = workdir / "STOP"
        self._init_cache: Weakness | None = None

    # -- infrastructure --------------------------------------------------------

    def _lock(self) -> AbstractContextManager[None]:
        return workdir_lock(self.workdir)

    def _stop_requested(self) -> bool:
        return self.stop.is_set() or self.stop_file.exists()

    def _steps_used(self) -> int:
        return sum(
            e.get("steps", 0)
            for e in self.journal.entries()
            if e["kind"] == "experiment" and e["event"] == "executed"
        )

    def _budget(self) -> Budget:
        return Budget(
            self.limits.max_steps_per_experiment,
            self.limits.max_tokens_per_experiment,
            self.limits.experiment_timeout_s,
        )

    # -- boucle ----------------------------------------------------------------

    def _check_world(self) -> None:
        previous = self.journal.loop_events("started")
        if previous and previous[0]["world_fingerprint"] != self.world.fingerprint:
            raise WorldMismatchError(
                f"journal produit sur le monde {previous[0]['world_fingerprint']}, "
                f"monde fourni {self.world.fingerprint}"
            )

    def run(self) -> dict[str, Any]:
        with self._lock():
            self._check_world()
            self.journal.loop(
                "started",
                world_fingerprint=self.world.fingerprint,
                limits=asdict(self.limits),
                policy=asdict(self.policy),
                pid=os.getpid(),
            )
            reason = "cycles épuisés"
            try:
                self._recover()
                for cycle in range(self.limits.max_cycles):
                    self._cycle(cycle)
            except _LoopStop as stop:
                reason = stop.reason
            summary = self.summary()
            self.journal.loop("stopped", reason=reason, summary=summary)
            self.log(f"boucle arrêtée : {reason}")
            return {"reason": reason, **summary}

    def run_directed(self, changes: dict[str, Any], *, target: str, reason: str) -> ExperimentRecord:
        """Expérience DIRIGÉE : l'opérateur pose une question précise (ex. « entraîner sur les
        solutions des tâches vues généralise-t-il ? ») sous la forme d'UNE modification de la
        recette de référence du champion. Seule la décision vient de l'extérieur : plan validé,
        entraînement réel, Judge indépendant, analyse d'échec, journal et registre sont ceux de la
        boucle, et le journal le dit (stratégie « directed »). La faiblesse citée doit avoir été
        mesurée ; aucune expérience sans champion attesté."""
        with self._lock():
            self._check_world()
            self._recover()
            obs = self._observe_with_integrity()
            if obs.champion is None:
                raise ValueError("expérience dirigée : aucun champion attesté à améliorer")
            weakness = next((w for w in obs.weaknesses if w.benchmark == target), None)
            if weakness is None:
                raise ValueError(
                    f"expérience dirigée : aucune faiblesse mesurée sur « {target} » "
                    f"(faiblesses : {[w.benchmark for w in obs.weaknesses]})"
                )
            reference = self.policy.reference(obs, self.world)
            recipe = replace(reference, **changes)
            change = "expérience dirigée : " + ", ".join(f"{k}={v}" for k, v in sorted(changes.items()))
            decision = StrategyDecision(
                DIRECTED,
                self.policy.hypothesis(self.world, weakness, DIRECTED, change),
                recipe,
                reference,
                None,
                [f"posée par l'opérateur : {reason}", f"faiblesse observée : {weakness.evidence}"],
            )
            plan = self._plan(obs, decision)
            try:
                self._check_global_limits()  # mêmes plafonds que la boucle autonome
                self.journal.loop("directed", experiment=plan.experiment_id, reason=reason, changes=changes)
                return self._run(plan, decision)
            except _LoopStop as stop:
                raise RuntimeError(f"expérience dirigée refusée : {stop.reason}") from stop

    def _cycle(self, cycle: int) -> None:
        if self._stop_requested():
            raise _LoopStop("arrêt demandé")
        obs = self._observe_with_integrity()
        self.journal.loop("cycle_started", cycle=cycle, observation=obs.to_dict())
        ids: list[str] = []
        if obs.champion is None:
            if self.bootstrap is None:
                raise _LoopStop("aucun champion et aucune recette d'amorçage")
            obs = replace(obs, weaknesses=[self._init_weakness()])
        exhausted = self._exhausted(obs)
        while len(ids) < self.limits.max_experiments_per_cycle:
            self._check_global_limits()
            decision = self.policy.decide(
                obs, self.world, self.journal, exhausted=exhausted, bootstrap=self.bootstrap
            )
            if decision is None:
                target = next((w.benchmark for w in obs.weaknesses if w.benchmark not in exhausted), None)
                if target is None:
                    break
                self.journal.loop(
                    "target_exhausted",
                    target=target,
                    champion=obs.champion.artifact if obs.champion else None,
                )
                exhausted.add(target)
                continue
            try:
                plan = self._plan(obs, decision)
            except (InvalidPlanError, NonFalsifiableHypothesisError) as exc:
                # Défense en profondeur : une décision invalide de la politique est consignée et
                # sa cible écartée pour ce champion — jamais un plantage, jamais une boucle infinie.
                self.journal.loop(
                    "plan_refused",
                    target=decision.hypothesis.target_benchmark,
                    strategy=decision.strategy,
                    reason=str(exc),
                    champion=obs.champion.artifact if obs.champion else None,
                )
                self.journal.loop(
                    "target_exhausted",
                    target=decision.hypothesis.target_benchmark,
                    champion=obs.champion.artifact if obs.champion else None,
                )
                exhausted.add(decision.hypothesis.target_benchmark)
                continue
            record = self._run(plan, decision)
            ids.append(record.experiment_id)
            if record.promoted:
                break
            analysis = record.analysis or {}
            if analysis.get("cause") in {c.value for c in INTEGRITY_CAUSES}:
                raise _LoopStop(f"alerte d'intégrité : {analysis.get('cause')} ({record.experiment_id})")
            if self._stop_requested():
                break
        comparison = self._compare(ids)
        self.journal.loop(
            "cycle_completed",
            cycle=cycle,
            experiments=ids,
            comparison=comparison,
            selection=self._select(comparison),
        )
        if not ids:
            if obs.weaknesses:
                raise _LoopStop(
                    "stratégies épuisées pour les faiblesses observées "
                    f"{[w.benchmark for w in obs.weaknesses]} (voir le journal : target_exhausted)"
                )
            raise _LoopStop("aucune faiblesse mesurée au-delà du seuil : rien à améliorer")

    def _check_global_limits(self) -> None:
        if len(self.journal.experiments()) >= self.limits.max_total_experiments:
            raise _LoopStop(f"plafond de {self.limits.max_total_experiments} expériences atteint")
        if self._steps_used() >= self.limits.max_total_steps:
            raise _LoopStop(f"budget de calcul atteint ({self._steps_used()} pas)")
        if self._stop_requested():
            raise _LoopStop("arrêt demandé")

    def _exhausted(self, obs: Observation) -> set[str]:
        champion = obs.champion.artifact if obs.champion else None
        return {
            e["target"] for e in self.journal.loop_events("target_exhausted") if e["champion"] == champion
        }

    def attest(self, entry: dict[str, Any]) -> str | None:
        """Vérifie que la version en service a été promue PAR CETTE BOUCLE (voir `attest`)."""
        return attest(
            self.registry.entries(),
            self.journal,
            entry,
            judge_id=JUDGE_ID,
            parent_world=self.world.spec.parent,
            artifact_exists=self.store.exists,
        )

    def adopt(self, parent_workdir: Path, *, reason: str) -> dict[str, Any]:
        """Monde ENFANT : le champion attesté du parent devient le point de départ, re-mesuré sur
        toutes les suites de ce monde (les nouvelles capacités comprises). Refusée sans parent
        déclaré, sur un registre non vide, ou si le champion du parent n'est pas attesté par la
        boucle du parent (ACCEPT du Judge indépendant consigné)."""
        with self._lock():
            self._check_world()
            if not self.world.spec.parent:
                raise ValueError("ce monde ne déclare aucun parent : rien à adopter")
            if self.registry.entries():
                raise ValueError("adoption refusée : le registre de ce monde n'est pas vide")
            parent_world = World.load(
                parent_workdir / "world.json", expected_fingerprint=self.world.spec.parent
            )
            parent = LoopController(parent_workdir, parent_world, log=lambda _m: None)
            champion = parent.registry.current()
            if champion is None:
                raise ValueError("le monde parent n'a aucun champion à adopter")
            problem = parent.attest(champion)
            if problem is not None:
                raise ValueError(f"champion du parent non attesté : {problem}")
            artifact = str(champion["artifact"])
            copied = self.store.put(
                parent.store.get(artifact), content_type="application/x-apex-checkpoint"
            )
            if copied.content_hash != artifact:
                raise ValueError("artefact du parent altéré pendant la copie")
            model = load_model_from_artifact(self.store, artifact, self.world)
            reports, details = measure_with_details(model, self.world)
            entry = self.registry.adopt(
                name=f"adopted:{champion['name']}",
                checkpoint_sha256=champion["checkpoint_sha256"],
                dataset_fingerprint=champion["dataset_fingerprint"],
                reports=reports,
                artifact=artifact,
                metadata={
                    **champion.get("metadata", {}),
                    "parent_world": parent_world.fingerprint,
                    "parent_registry_index": champion["index"],
                    "reason": reason,
                },
            )
            self.journal.loop(
                "adopted",
                registry_index=entry["index"],
                artifact=artifact,
                parent_world=parent_world.fingerprint,
                parent_registry_index=champion["index"],
                parent_champion=champion["name"],
                reason=reason,
                reports=reports_to_dict(reports),
                capability_details=details,
            )
            self.log(f"adoption de « {champion['name']} » du monde {parent_world.fingerprint[:19]}")
            return entry

    def _observe_with_integrity(self) -> Observation:
        obs = observe(
            self.world, self.registry, self.journal, self.store, min_gap=self.limits.min_weakness_gap
        )
        while obs.champion is not None:
            current = self.registry.current()
            assert current is not None
            attestation = self.attest(current)
            if obs.champion.integrity_ok and attestation is None:
                break
            detail = obs.champion.integrity_detail if not obs.champion.integrity_ok else attestation
            try:
                entry = self.registry.rollback(f"champion inutilisable ou non attesté ({detail})")
            except LookupError as exc:
                self.journal.loop("rollback_failed", champion=obs.champion.name, detail=detail)
                raise _LoopStop(f"champion rejeté ({detail}) et aucune version antérieure : {exc}") from exc
            self.journal.loop(
                "rollback",
                from_champion=obs.champion.name,
                to=entry["name"],
                reason=detail,
                registry_index=entry["index"],
            )
            self.log(f"retour arrière vers {entry['name']} : {detail}")
            obs = observe(
                self.world, self.registry, self.journal, self.store, min_gap=self.limits.min_weakness_gap
            )
        return obs

    # -- plans -------------------------------------------------------------------

    def _init_weakness(self) -> Weakness:
        assert self.bootstrap is not None
        if self._init_cache is None:
            # Seule la suite de texte est utile ici : pas d'exécution de capacité superflue.
            init = measure_text(initial_model(self.world, self.bootstrap.seed), self.world)[PRIMARY]
            self._init_cache = Weakness(
                benchmark=PRIMARY,
                domain=None,
                value=round(init.bits_per_byte, 5),
                reference=0.0,
                gap=round(init.bits_per_byte, 5),
                documents=len(init.scores),
                evidence=(
                    f"aucun champion : l'initialisation mesure {init.bits_per_byte:.4f} bpb "
                    f"sur « {PRIMARY} »"
                ),
            )
        return self._init_cache

    def _plan(self, obs: Observation, decision: StrategyDecision) -> ExperimentPlan:
        baseline: dict[str, Any] = (
            {"name": obs.champion.name, "index": obs.champion.index, "artifact": obs.champion.artifact}
            if obs.champion
            else {"name": "init", "artifact": None}
        )
        plan = ExperimentPlan(
            self.world.fingerprint,
            baseline,
            decision.baseline_recipe,
            decision.recipe,
            decision.hypothesis,
            self._budget(),
            parent_experiment_id=decision.parent_experiment_id,
        )
        plan.validate(
            self.world.suite_names,
            max_modifications=self.limits.max_modifications,
            domains=set(self.world.spec.domains),
        )
        return plan

    # -- une expérience ----------------------------------------------------------

    def _run(self, plan: ExperimentPlan, decision: StrategyDecision) -> ExperimentRecord:
        previous = self.journal.by_plan(plan.fingerprint)
        if previous:
            # Défense en profondeur : la politique déduplique déjà. Seule une nouvelle tentative
            # bornée, après des échecs de cause inconnue ou de délai, peut répéter un plan.
            retry_ok = (
                decision.retry_of == previous[-1].experiment_id
                and len(previous) <= self.limits.max_retries
                and all(r.finished and (r.analysis or {}).get("cause") in RETRYABLE for r in previous)
            )
            if not retry_ok:
                raise DuplicateExperimentError(
                    f"plan {plan.fingerprint} déjà exécuté : {[r.experiment_id for r in previous]}"
                )
        if self._steps_used() + plan.recipe.steps > self.limits.max_total_steps:
            raise _LoopStop(f"budget de calcul insuffisant pour {plan.recipe.steps} pas")
        prior, sources = self._variance_prior()
        self.journal.experiment(
            plan.experiment_id,
            "planned",
            plan=plan.to_dict(),
            decision=decision.to_dict(),
            # Historique de variance FIGÉ à la planification : un résultat ultérieur ne change jamais
            # les règles appliquées à cette expérience.
            variance_prior=prior,
            variance_sources=sources,
        )
        self.log(f"[{plan.experiment_id}] {plan.hypothesis.strategy} : {plan.hypothesis.modification}")
        return self._conclude(plan, "planned")

    def _conclude(self, plan: ExperimentPlan, from_event: str) -> ExperimentRecord:
        exp = plan.experiment_id
        if from_event in ("planned", "started", "interrupted", "recovered"):
            self.journal.experiment(exp, "started", run_dir=str(self._run_dir(exp)))
            progress = {"step": 0}
            live = Telemetry(self._run_dir(exp))
            live.emit(
                telemetry.TRAINING_STARTED,
                steps=plan.recipe.steps,
                tokens=plan.recipe.tokens,
                lr=plan.recipe.lr,
                mix=plan.recipe.mix,
            )
            try:
                result = execute(
                    plan,
                    self.world,
                    self.store,
                    self._run_dir(exp),
                    stop=self.stop,
                    stop_file=self.stop_file,
                    checkpoint_every=self.limits.checkpoint_every,
                    on_step=lambda step: progress.__setitem__("step", step),
                    on_event=live.emit,
                )
            except StopRequested as stop:
                self.journal.experiment(exp, "interrupted", detail=str(stop))
                raise _LoopStop("arrêt demandé pendant une expérience (reprise possible)") from stop
            except Exception as exc:
                failure = analyze_exception(plan, exc)
                # Le calcul consommé compte dans le budget même quand l'expérience échoue.
                self.journal.experiment(
                    exp,
                    "executed",
                    ok=False,
                    error=f"{exc.__class__.__name__}: {exc}",
                    steps=progress["step"],
                )
                self.journal.experiment(exp, "analyzed", analysis=failure.to_dict())
                self.journal.experiment(exp, "aborted", reason=failure.cause.value)
                self.log(f"[{exp}] échec d'exécution : {failure.cause.value}")
                return self.journal.experiments()[exp]
            live.emit(telemetry.TRAINING_COMPLETED, steps=result.steps, duration_s=result.duration_s)
            self.journal.experiment(
                exp,
                "executed",
                ok=True,
                artifact=result.artifact,
                checkpoint_sha256=result.checkpoint_sha256,
                claimed_reports=reports_to_dict(result.claimed_reports),
                curve=result.curve,
                steps=result.steps,
                tokens_seen=result.tokens_seen,
                training_candidate_ids=result.training_candidate_ids,
                resumed_from_step=result.resumed_from_step,
                duration_s=result.duration_s,
                software=result.software,
            )
            from_event = "executed"
        record = self.journal.experiments()[exp]
        executed = record.payload("executed")
        assert executed is not None and executed["ok"]
        if from_event == "executed":
            current = self.registry.current()
            stored = None
            if plan.baseline.get("artifact"):
                if current is None or current.get("artifact") != plan.baseline["artifact"]:
                    self.journal.experiment(
                        exp, "aborted", reason="champion changé depuis la planification"
                    )
                    return self.journal.experiments()[exp]
                stored = {n: EvalReport.from_dict(r) for n, r in current["reports"].items()}
            live = Telemetry(self._run_dir(exp))
            live.emit(telemetry.JUDGE_STARTED)
            try:
                verdict = self.judge.judge(
                    candidate_artifact=executed["artifact"],
                    claimed_reports={
                        n: EvalReport.from_dict(r) for n, r in executed["claimed_reports"].items()
                    },
                    training_candidate_ids=executed["training_candidate_ids"],
                    baseline_artifact=plan.baseline.get("artifact"),
                    baseline_seed=plan.recipe.seed,
                    baseline_stored_reports=stored,
                    plan=plan,
                    replay_dir=self._run_dir(exp) / "judge_replay",
                    stop=self.stop,
                    stop_file=self.stop_file,
                    protections=self._protections(),
                    on_event=lambda phase, **fields: live.emit(phase, run="replay", **fields),
                )
            except StopRequested as stop:
                # Rien n'est consigné : l'expérience reste « executed » et sera re-jugée.
                raise _LoopStop("arrêt demandé pendant le jugement (reprise possible)") from stop
            self.journal.experiment(
                exp,
                "judged",
                result=verdict.to_dict(),
                candidate_reports=reports_to_dict(verdict.candidate_reports),
                # Mémoire des échecs par tâche : chaque tentative EXÉCUTÉE par le Judge (édition,
                # tests passés, première cause d'échec), jamais les affirmations de l'exécuteur.
                capability_details=verdict.candidate_details,
            )
            self.log(f"[{exp}] Judge : {verdict.decision}")
            from_event = "judged"
            judged = verdict
        else:
            judged = self._judge_from_journal(record)
        record = self.journal.experiments()[exp]
        if (
            self.limits.seeds >= 2
            and plan.baseline.get("artifact")
            and (from_event in ("judged", "replicated") or record.payload("seed_verdict") is not None)
        ):
            return self._conclude_seeds(plan, from_event)
        if from_event == "judged" and judged.accepted and self.limits.replicate_promotions:
            try:
                replica = self._replicate(plan)
            except StopRequested as stop:
                # Rien n'est consigné : l'expérience reste « judged » et la réplique sera refaite.
                raise _LoopStop("arrêt demandé pendant la réplique (reprise possible)") from stop
            except Exception as exc:  # une réplique qui échoue ne confirme rien
                replica = {
                    "seed": plan.recipe.seed + REPLICA_SEED_OFFSET,
                    "artifact": None,
                    "decision": "error",
                    "accepted": False,
                    "reasons": [f"{exc.__class__.__name__}: {exc}"],
                    "comparisons": [],
                }
            self.journal.experiment(exp, "replicated", **replica)
            self.log(f"[{exp}] réplique (graine {replica['seed']}) : {replica['decision']}")
            judged = self._judge_from_journal(self.journal.experiments()[exp])
            from_event = "replicated"
        if from_event in ("judged", "replicated"):
            analysis = analyze_judgement(
                plan,
                judged,
                guard_tolerance=self.judge.max_guard_regression_bpb,
                tolerances=self.world.metric_tolerances,
            )
            self.journal.experiment(
                exp,
                "analyzed",
                analysis=analysis.to_dict() if analysis else {"cause": None, "accepted": True},
            )
            from_event = "analyzed"
        if from_event == "analyzed":
            self._promote(plan, record=self.journal.experiments()[exp], judged=judged)
        return self.journal.experiments()[exp]

    def _replicate(self, plan: ExperimentPlan, index: int = 1) -> dict[str, Any]:
        """Même plan, graine indépendante n° `index` : entraînement réel puis jugement complet
        (rejeu compris) contre la MÊME référence. Ses mesures par élément sont consignées : elles
        entrent dans le verdict multi-graines et dans l'historique de variance."""
        suffix = "" if index == 1 else str(index)
        replica_plan = replace(
            plan,
            recipe=replace(plan.recipe, seed=plan.recipe.seed + index * REPLICA_SEED_OFFSET),
            experiment_id=f"{plan.experiment_id}-replica{suffix}",
        )
        run_dir = self._run_dir(plan.experiment_id) / f"replica{suffix}"
        tag = f"replica{suffix}"
        live = Telemetry(self._run_dir(plan.experiment_id))
        live.emit(telemetry.REPLICATION_STARTED, seed=replica_plan.recipe.seed, seed_index=index)
        result = execute(
            replica_plan,
            self.world,
            self.store,
            run_dir,
            stop=self.stop,
            stop_file=self.stop_file,
            checkpoint_every=self.limits.checkpoint_every,
            on_event=lambda phase, **fields: live.emit(phase, run=tag, **fields),
        )
        current = self.registry.current()
        stored = (
            {n: EvalReport.from_dict(r) for n, r in current["reports"].items()}
            if plan.baseline.get("artifact") and current is not None
            else None
        )
        verdict = self.judge.judge(
            candidate_artifact=result.artifact,
            claimed_reports=result.claimed_reports,
            training_candidate_ids=result.training_candidate_ids,
            baseline_artifact=plan.baseline.get("artifact"),
            baseline_seed=replica_plan.recipe.seed,
            baseline_stored_reports=stored,
            plan=replica_plan,
            replay_dir=run_dir / "judge_replay",
            stop=self.stop,
            stop_file=self.stop_file,
            protections=self._protections(),
            on_event=lambda phase, **fields: live.emit(phase, run=f"{tag}_replay", **fields),
        )
        return {
            "seed": replica_plan.recipe.seed,
            "seed_index": index,
            "artifact": result.artifact,
            "decision": verdict.decision,
            "accepted": verdict.accepted,
            "reasons": verdict.reasons[:3],
            "comparisons": verdict.to_dict()["comparisons"],
            "candidate_reports": reports_to_dict(verdict.candidate_reports),
            "invalid": failed_integrity(verdict.checks),
        }

    # -- protocole multi-graines (P3.7) ----------------------------------------------------

    def _conclude_seeds(self, plan: ExperimentPlan, from_event: str) -> ExperimentRecord:
        exp = plan.experiment_id
        record = self.journal.experiments()[exp]
        if from_event in ("judged", "replicated"):
            verdict = self._seed_verdict(plan, record)
            while verdict["decision"] != INVALID and verdict["need_more_seeds"]:
                index = len(record.replications) + 1
                try:
                    replica = self._replicate(plan, index)
                except StopRequested as stop:
                    # Rien n'est consigné : la graine sera refaite à la reprise.
                    raise _LoopStop(
                        "arrêt demandé pendant une graine supplémentaire (reprise possible)"
                    ) from stop
                except Exception as exc:  # une graine qui échoue invalide l'expérience
                    replica = {
                        "seed": plan.recipe.seed + index * REPLICA_SEED_OFFSET,
                        "seed_index": index,
                        "artifact": None,
                        "decision": "error",
                        "accepted": False,
                        "reasons": [f"{exc.__class__.__name__}: {exc}"],
                        "comparisons": [],
                        "candidate_reports": None,
                        "invalid": ["replica_error"],
                    }
                self.journal.experiment(exp, "replicated", **replica)
                self.log(f"[{exp}] graine {index} ({replica['seed']}) : {replica['decision']}")
                record = self.journal.experiments()[exp]
                verdict = self._seed_verdict(plan, record)
            self.journal.experiment(exp, "seed_verdict", result=verdict)
            self.log(
                f"[{exp}] verdict sur {verdict['k']} graines : {verdict['decision']} "
                f"— {verdict['reasons'][:1]}"
            )
            from_event = "seed_verdict"
        record = self.journal.experiments()[exp]
        stored = record.payload("seed_verdict")
        assert stored is not None
        verdict = stored["result"]
        if from_event == "seed_verdict":
            judged = record.payload("judged") or {}
            analysis = analyze_seed_verdict(plan, verdict, judged.get("result", {}).get("checks", {}))
            self.journal.experiment(
                exp,
                "analyzed",
                analysis=analysis.to_dict() if analysis else {"cause": None, "accepted": True},
            )
            from_event = "analyzed"
        if from_event == "analyzed":
            self._promote_seeds(plan, record=self.journal.experiments()[exp], verdict=verdict)
        return self.journal.experiments()[exp]

    def _variance_settings(self, plan: ExperimentPlan) -> dict[str, Any]:
        target = self.judge.primary_for(plan)
        capability = self.world.capability_of_suite(target)
        memorization = (
            self.world.capabilities[capability][0].contract.memorization_suite if capability else None
        )
        return {
            "target": target,
            "memorization_suite": memorization,
            "capability_target": capability is not None,
            "max_seeds": self.limits.max_seeds,
            "tolerances": {
                **self.world.metric_tolerances,
                "bits_per_byte": self.judge.max_guard_regression_bpb,
            },
        }

    def _seed_reports(self, record: ExperimentRecord) -> tuple[list[dict[str, EvalReport]], list[str]]:
        judged = record.payload("judged") or {}
        seeds = [{n: EvalReport.from_dict(r) for n, r in judged.get("candidate_reports", {}).items()}]
        invalid = [f"{c}#0" for c in failed_integrity(judged.get("result", {}).get("checks", {}))]
        for i, replica in enumerate(record.replications, start=1):
            invalid += [f"{c}#{i}" for c in replica.get("invalid") or []]
            if replica.get("candidate_reports"):
                seeds.append({n: EvalReport.from_dict(r) for n, r in replica["candidate_reports"].items()})
            elif not replica.get("invalid"):
                invalid.append(f"graine sans mesures par élément#{i}")
        return seeds, invalid

    def _seed_verdict(self, plan: ExperimentPlan, record: ExperimentRecord) -> dict[str, Any]:
        seeds, invalid = self._seed_reports(record)
        planned = record.payload("planned") or {}
        prior = planned.get("variance_prior")
        if prior is None:  # expérience planifiée avant P3.7 : historique calculé à la reprise
            prior = self._variance_prior()[0]
        if not invalid and len(seeds) < self.limits.seeds:
            return {
                "decision": "pending",
                "k": len(seeds),
                "need_more_seeds": True,
                "reasons": [],
                "rows": [],
            }
        settings = self._variance_settings(plan)
        return {
            **seed_verdict(
                seeds=seeds,
                protections=self._protections(),
                tolerances=settings["tolerances"],
                prior=prior,
                target=settings["target"],
                memorization_suite=settings["memorization_suite"],
                new_capability=settings["capability_target"],
                invalid=invalid,
                max_seeds=self.limits.max_seeds,
            ),
            "prior": prior,
        }

    def _promote_seeds(
        self, plan: ExperimentPlan, *, record: ExperimentRecord, verdict: dict[str, Any]
    ) -> None:
        exp = plan.experiment_id
        if verdict["decision"] != ACCEPT:
            self.journal.experiment(
                exp,
                "promotion",
                promoted=False,
                reason=f"verdict sur {verdict['k']} graines : {verdict['decision']} "
                f"— {'; '.join(verdict['reasons'][:2])}",
            )
            return
        executed = record.payload("executed")
        judged = record.payload("judged")
        assert executed is not None and judged is not None
        seeds, _ = self._seed_reports(record)
        settings = self._variance_settings(plan)
        existing = next(
            (e for e in self.registry.entries() if e.get("metadata", {}).get("experiment_id") == exp), None
        )
        entry = existing or self.registry.propose(
            Proposal(
                name=exp,
                checkpoint_sha256=executed["checkpoint_sha256"],
                dataset_fingerprint=self.world.dataset.fingerprint,
                reports=seeds[0],
                in_run_decision=ACCEPT,
                artifact=executed["artifact"],
                primary=settings["target"],
                metadata={
                    "experiment_id": exp,
                    "recipe": plan.recipe.to_dict(),
                    "plan_fingerprint": plan.fingerprint,
                    "judge_id": judged["result"].get("judge_id"),
                    "world": self.world.fingerprint,
                    "seeds": len(seeds),
                },
                seed_reports=tuple(seeds[1:]),
                variance={**settings, "prior": verdict["prior"]},
            )
        )
        self.journal.experiment(
            exp,
            "promotion",
            promoted=entry["promoted"],
            registry_index=entry["index"],
            reason="; ".join(entry["reasons"]),
        )
        self.log(f"[{exp}] promotion : {'OUI' if entry['promoted'] else 'NON'}")

    def verify(self) -> dict[str, Any]:
        """Vérification COMPLÈTE d'un répertoire, en lecture seule (après un arrêt, un redémarrage,
        une copie) : monde relu (empreintes), chaînes du journal et du registre, journal produit sur
        ce monde, attestation de chaque version promue, contenu de CHAQUE artefact référencé
        (champion, candidats, graines) re-haché, expériences inachevées à reprendre."""
        problems: list[str] = []
        entries = self.registry.entries()  # lève si la chaîne est rompue
        journal = self.journal.entries()  # idem
        started = [e for e in journal if e["kind"] == "loop" and e["event"] == "started"]
        if any(e["world_fingerprint"] != self.world.fingerprint for e in started):
            problems.append("journal produit sur un autre monde")
        for entry in entries:
            if entry["promoted"]:
                problem = self.attest(entry)
                if problem:
                    problems.append(f"version #{entry['index']} non attestée : {problem}")
        artifacts: dict[str, str] = {}
        for entry in entries:
            if entry.get("artifact"):
                artifacts[entry["artifact"]] = f"registre #{entry['index']}"
        for record in self.journal.experiments().values():
            executed = record.payload("executed") or {}
            if executed.get("artifact"):
                artifacts.setdefault(executed["artifact"], f"{record.experiment_id} (graine 0)")
            for replica in record.replications:
                if replica.get("artifact"):
                    artifacts.setdefault(
                        replica["artifact"],
                        f"{record.experiment_id} (graine {replica.get('seed_index', 1)})",
                    )
        missing = 0
        for artifact, origin in sorted(artifacts.items()):
            try:
                self.store.get(artifact)  # re-hache le contenu
            except LookupError:
                missing += 1
                problems.append(f"artefact absent : {artifact[:19]}… ({origin})")
            except Exception as exc:
                problems.append(f"artefact altéré : {artifact[:19]}… ({origin}) : {exc.__class__.__name__}")
        current = self.registry.current()
        return {
            "ok": not problems,
            "problems": problems,
            "world": self.world.fingerprint,
            "parent_world": self.world.spec.parent,
            "champion": current["name"] if current else None,
            "registry_entries": len(entries),
            "journal_entries": len(journal),
            "artifacts_checked": len(artifacts),
            "artifacts_missing": missing,
            "unfinished": [r.experiment_id for r in self.journal.unfinished()],
        }

    def import_replications(self, sources: list[Path]) -> dict[str, Any]:
        """Historique de variance IMPORTÉ d'autres répertoires (lignée) : pour chaque recette
        affinée depuis un champion, les valeurs par suite de ses graines (candidat, répliques, ou
        expériences de même recette ne différant que par la graine), avec leurs empreintes de
        benchmark et leur provenance. Graines identiques dédupliquées (un rejeu déterministe n'est
        pas une nouvelle graine). Chaînes vérifiées ; rien n'est modifié dans les sources."""
        seeds: dict[str, dict[int, dict[str, Any]]] = {}
        origin: dict[str, dict[str, Any]] = {}
        for source in sources:
            journal = ExperimentJournal(source / "journal.jsonl")
            entries = journal.entries()  # vérifie la chaîne
            head = entries[-1]["hash"] if entries else None
            for record in journal.experiments().values():
                judged = record.payload("judged")
                recipe = dict(record.plan.get("recipe", {}))
                if (
                    judged is None
                    or recipe.get("init") != "champion"
                    or not judged.get("candidate_reports")
                ):
                    continue
                seed = int(recipe.pop("seed"))
                key = repr(
                    (
                        record.plan.get("world_fingerprint"),
                        record.plan.get("baseline", {}).get("artifact"),
                        sorted(recipe.items()),
                    )
                )
                reports = judged["candidate_reports"]
                group = seeds.setdefault(key, {})
                group.setdefault(
                    seed,
                    {
                        "values": {s: r["value"] for s, r in reports.items()},
                        "fingerprints": {s: r["benchmark_fingerprint"] for s, r in reports.items()},
                        "from": f"{record.experiment_id}@{source}",
                    },
                )
                for replica in record.replications:
                    if replica.get("invalid") or replica.get("artifact") is None:
                        continue
                    measured = replica.get("candidate_reports")
                    values = (
                        {s: r["value"] for s, r in measured.items()}
                        if measured
                        else {c["suite"]: c["candidate_value"] for c in replica.get("comparisons", [])}
                    )
                    group.setdefault(
                        int(replica["seed"]),
                        {
                            "values": values,
                            "fingerprints": {s: r["benchmark_fingerprint"] for s, r in reports.items()},
                            "from": f"{record.experiment_id}#réplique@{source}",
                        },
                    )
                origin.setdefault(key, {"workdir": str(source), "journal_head": head})
        groups = []
        for key, by_seed in seeds.items():
            if len(by_seed) < 2:
                continue
            members = list(by_seed.values())
            suites = set.intersection(*(set(m["values"]) for m in members))
            groups.append(
                {
                    "source": {
                        **origin[key],
                        "seeds": sorted(by_seed),
                        "members": [m["from"] for m in members],
                    },
                    "values": {s: [m["values"][s] for m in members] for s in sorted(suites)},
                    "fingerprints": {s: members[0]["fingerprints"][s] for s in sorted(suites)},
                }
            )
        with self._lock():
            self.journal.loop("replication_history", groups=groups)
        prior, _ = self._variance_prior()
        return {"groups": len(groups), "sources": [g["source"] for g in groups], "prior": prior}

    def _suite_fingerprints(self) -> dict[str, str]:
        prints = {name: bench.fingerprint for name, bench in self.world.benchmarks.items()}
        for capability, data in self.world.capabilities.values():
            prints.update(capability.suite_fingerprints(data))
        return prints

    def _variance_prior(self) -> tuple[dict[str, float], list[dict[str, Any]]]:
        """sigma par suite, groupé sur l'HISTORIQUE des réplications IMPORTÉ (événement
        `replication_history`), suites dont le benchmark a l'empreinte de ce monde.

        Les expériences multi-graines de ce journal n'y entrent PAS : une expérience destructrice
        a des graines très dispersées sur les suites qu'elle détruit, et les regrouper élargissait
        la marge des expériences suivantes (vulnérabilité démontrée en P3.7, boucle L : marge
        texte 0,097 au lieu de 0,010). Leur dispersion reste prise en compte, expérience par
        expérience, dans l'incertitude (s_obs), jamais dans la marge."""
        prints = self._suite_fingerprints()
        groups: list[dict[str, list[float]]] = []
        sources: list[dict[str, Any]] = []
        for event in self.journal.loop_events("replication_history"):
            for group in event["groups"]:
                values = {
                    s: v
                    for s, v in group["values"].items()
                    if group["fingerprints"].get(s) == prints.get(s)
                }
                if values:
                    groups.append(values)
                    sources.append(group["source"])
        return pooled_sigma(groups), sources

    def _judge_from_journal(self, record: ExperimentRecord) -> JudgeResult:
        judged = self._first_judgement(record)
        replicated = record.payload("replicated")
        if replicated is None or replicated["accepted"]:
            return judged
        # La décision finale est REJECT : le gain n'a pas tenu avec une graine indépendante.
        return replace(
            judged,
            decision=JudgeDecision.REJECT.value,
            reasons=[
                f"gain non répliqué : la réplique (graine {replicated['seed']}) est jugée "
                f"{replicated['decision']} — {'; '.join(replicated['reasons'][:1])}",
                *judged.reasons,
            ],
            replicated=False,
            replica_comparisons=replicated.get("comparisons", []),
        )

    def _first_judgement(self, record: ExperimentRecord) -> JudgeResult:
        judged = record.payload("judged")
        assert judged is not None
        result = judged["result"]
        return JudgeResult(
            decision=result["decision"],
            reasons=result["reasons"],
            checks=result["checks"],
            comparisons=result["comparisons"],
            candidate_reports={n: EvalReport.from_dict(r) for n, r in judged["candidate_reports"].items()},
            primary=result.get("primary", PRIMARY),
            task_changes=result.get("task_changes", {}),
            learning=result.get("learning", {}),
            forgetting=result.get("forgetting", []),
        )

    def _protections(self) -> list[Protection]:
        """Ancres des capacités acquises, depuis les seules versions ATTESTÉES du registre."""
        attested = [e for e in self.registry.entries() if not e["promoted"] or self.attest(e) is None]
        return protections(attested, self.world.protected_groups)

    def _promote(self, plan: ExperimentPlan, *, record: ExperimentRecord, judged: JudgeResult) -> None:
        exp = plan.experiment_id
        if not judged.accepted:
            self.journal.experiment(
                exp,
                "promotion",
                promoted=False,
                reason=f"Judge : {judged.decision} — {'; '.join(judged.reasons[:2])}",
            )
            return
        executed = record.payload("executed")
        assert executed is not None
        existing = next(
            (e for e in self.registry.entries() if e.get("metadata", {}).get("experiment_id") == exp), None
        )
        entry = existing or self.registry.propose(
            Proposal(
                name=exp,
                checkpoint_sha256=executed["checkpoint_sha256"],
                dataset_fingerprint=self.world.dataset.fingerprint,
                reports=judged.candidate_reports,
                in_run_decision=judged.decision,
                artifact=executed["artifact"],
                primary=judged.primary,
                metadata={
                    "experiment_id": exp,
                    "recipe": plan.recipe.to_dict(),
                    "plan_fingerprint": plan.fingerprint,
                    "judge_id": judged.judge_id,
                    "world": self.world.fingerprint,
                },
            )
        )
        self.journal.experiment(
            exp,
            "promotion",
            promoted=entry["promoted"],
            registry_index=entry["index"],
            reason="; ".join(entry["reasons"]),
        )
        self.log(f"[{exp}] promotion : {'OUI' if entry['promoted'] else 'NON'}")

    def _run_dir(self, experiment_id: str) -> Path:
        return self.workdir / "runs" / experiment_id

    # -- reprise -----------------------------------------------------------------

    def _recover(self) -> None:
        for record in self.journal.unfinished():
            plan = ExperimentPlan.from_dict(record.plan)
            last = record.status
            self.journal.experiment(plan.experiment_id, "recovered", from_event=last)
            self.log(f"[{plan.experiment_id}] reprise après interruption (dernière étape : {last})")
            if plan.world_fingerprint != self.world.fingerprint:
                self.journal.experiment(plan.experiment_id, "aborted", reason="monde différent")
                continue
            self._conclude(plan, last)

    # -- comparaison -------------------------------------------------------------

    def _compare(self, ids: list[str]) -> list[dict[str, Any]]:
        records = self.journal.experiments()
        rows = []
        for exp in ids:
            r = records[exp]
            judged = r.payload("judged")
            candidate = (judged or {}).get("result", {}).get("candidate", {})
            rows.append(
                {
                    "experiment_id": exp,
                    "strategy": r.plan.get("hypothesis", {}).get("strategy"),
                    "decision": r.decision,
                    "cause": (r.analysis or {}).get("cause"),
                    "promoted": r.promoted,
                    "heldout_bpb": candidate.get(PRIMARY, {}).get("bits_per_byte"),
                    "target": r.plan.get("target_benchmark"),
                    # valeur de la métrique de la cible (BPB d'un texte, coût d'une capacité)
                    "target_value": candidate.get(r.plan.get("target_benchmark", ""), {}).get("value"),
                }
            )
        return rows

    def _select(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Sélection du meilleur à l'issue du cycle : seule une version promue
        (ACCEPT du Judge puis comparaison du registre) peut être retenue ;
        sinon le champion est conservé. Le meilleur candidat REJETÉ est cité
        à titre d'information, jamais retenu."""
        current = self.registry.current()
        promoted = [r for r in rows if r["promoted"]]
        measured = [r for r in rows if r["target_value"] is not None and not r["promoted"]]
        best_rejected = min(measured, key=lambda r: r["target_value"]) if measured else None
        return {
            "selected": promoted[-1]["experiment_id"]
            if promoted
            else (current["name"] if current else None),
            "reason": "promu (ACCEPT du Judge, confirmé par le registre)"
            if promoted
            else "aucun candidat accepté : champion conservé",
            "best_rejected": {
                "experiment_id": best_rejected["experiment_id"],
                "target": best_rejected["target"],  # les cibles peuvent différer d'une ligne à l'autre
                "target_value": best_rejected["target_value"],
                "cause": best_rejected["cause"],
            }
            if best_rejected
            else None,
        }

    def summary(self) -> dict[str, Any]:
        records = self.journal.experiments()
        current = self.registry.current()
        return {
            "experiments": len(records),
            "promoted": [r.experiment_id for r in records.values() if r.promoted],
            "rejected": [r.experiment_id for r in records.values() if r.finished and not r.promoted],
            "unfinished": [r.experiment_id for r in records.values() if not r.finished],
            "champion": current["name"] if current else None,
            "steps_used": self._steps_used(),
        }
