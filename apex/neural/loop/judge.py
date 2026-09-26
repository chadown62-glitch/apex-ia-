"""Judge INDÉPENDANT de l'agent qui formule et exécute les hypothèses.

Il ne reçoit de l'exécuteur que des RÉFÉRENCES (artefacts, identifiants de
documents d'entraînement) et des AFFIRMATIONS (mesures déclarées) ; il ne
leur fait jamais confiance. Contrôles, dans cet ordre :

1. intégrité : chaque checkpoint est relu depuis l'ArtifactStore (hash
   vérifié), doit avoir été entraîné sur le dataset du monde, avec son
   tokenizer ;
2. contamination : les documents d'entraînement déclarés doivent tous
   appartenir au split train du monde (jamais validation ni held-out) ;
3. mesure : le Judge mesure LUI-MÊME champion et candidat, deux fois chacun
   (rechargement indépendant) : les deux mesures doivent être identiques ;
4. sincérité : les mesures déclarées par l'exécuteur doivent être celles du
   Judge ; les mesures du champion enregistrées au registre doivent être
   celles que le Judge obtient aujourd'hui (baseline non modifiée après coup) ;
5. identité : un candidat aux poids identiques au champion n'est pas une
   amélioration ;
6. provenance (quand le plan est fourni) : les documents déclarés doivent
   être exactement ceux que la recette tire du split train, et le Judge
   REJOUE l'entraînement de la recette : le checkpoint obtenu doit être
   identique bit à bit au candidat. Un exécuteur qui aurait entraîné sur
   autre chose (données d'évaluation, autre recette) tout en déclarant la
   recette est ainsi démasqué ;
7. statistique : `NeuralJudge` (bootstrap apparié, toutes les suites du monde
   protégées).

Un seul contrôle en échec suffit à rejeter. Les critères sont ceux du monde,
jamais ceux proposés par le plan."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apex.artifacts.provider import ArtifactStoreProvider
from apex.errors import TimeoutExceeded
from apex.neural.dataset import Split
from apex.neural.evaluation import EvalReport, JudgeDecision, NeuralJudge
from apex.neural.loop.execute import (
    StopRequested,
    execute,
    initial_model,
    load_model_from_artifact,
    measure,
)
from apex.neural.loop.plan import ExperimentPlan
from apex.neural.loop.world import PRIMARY, World
from apex.neural.model import ApexLM

JUDGE_ID = "apex-neural-independent-judge/1"


@dataclass(frozen=True)
class JudgeResult:
    decision: str
    reasons: list[str]
    checks: dict[str, dict[str, Any]]
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    candidate_reports: dict[str, EvalReport] = field(default_factory=dict)
    baseline_reports: dict[str, EvalReport] = field(default_factory=dict)
    judge_id: str = JUDGE_ID

    @property
    def accepted(self) -> bool:
        return self.decision == JudgeDecision.ACCEPT.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id,
            "decision": self.decision,
            "reasons": self.reasons,
            "checks": self.checks,
            "comparisons": self.comparisons,
            "candidate": {n: r.summary() for n, r in self.candidate_reports.items()},
            "baseline": {n: r.summary() for n, r in self.baseline_reports.items()},
        }


def _same_scores(a: dict[str, EvalReport], b: dict[str, EvalReport]) -> bool:
    if set(a) != set(b):
        return False
    return all(
        a[name].benchmark_fingerprint == b[name].benchmark_fingerprint
        and [(s.doc_id, s.bits_per_byte) for s in a[name].scores]
        == [(s.doc_id, s.bits_per_byte) for s in b[name].scores]
        for name in a
    )


class IndependentJudge:
    def __init__(
        self, world: World, store: ArtifactStoreProvider, *, max_guard_regression_bpb: float = 0.01
    ) -> None:
        self.world = world
        self.store = store
        self.max_guard_regression_bpb = max_guard_regression_bpb

    def _measure_twice(self, load: Any) -> tuple[dict[str, EvalReport], bool, ApexLM]:
        first_model = load()
        first = measure(first_model, self.world)
        second = measure(load(), self.world)
        return first, _same_scores(first, second), first_model

    def _replay(
        self,
        plan: ExperimentPlan,
        candidate_artifact: str,
        replay_dir: Path,
        stop: threading.Event | None,
        stop_file: Path | None,
        check: Any,
    ) -> None:
        try:
            replayed = execute(plan, self.world, self.store, replay_dir, stop=stop, stop_file=stop_file)
        except StopRequested:
            raise
        except TimeoutExceeded as exc:
            check("training_replayed", False, f"rejeu interrompu : {exc}", cause="timeout")
            return
        except Exception as exc:
            check("training_replayed", False, f"rejeu impossible : {exc.__class__.__name__}: {exc}")
            return
        same = replayed.artifact == candidate_artifact
        check(
            "training_replayed",
            same,
            f"rejeu de la recette : checkpoint identique bit à bit ({replayed.artifact[:19]}…)"
            if same
            else f"le rejeu de la recette donne {replayed.artifact[:19]}…, "
            f"le candidat est {candidate_artifact[:19]}… : il n'a pas été produit par ce plan",
        )

    def judge(
        self,
        *,
        candidate_artifact: str,
        claimed_reports: dict[str, EvalReport],
        training_candidate_ids: list[str],
        baseline_artifact: str | None,
        baseline_seed: int,
        baseline_stored_reports: dict[str, EvalReport] | None = None,
        plan: ExperimentPlan | None = None,
        replay_dir: Path | None = None,
        stop: threading.Event | None = None,
        stop_file: Path | None = None,
    ) -> JudgeResult:
        """`plan` + `replay_dir` activent le contrôle de provenance par
        rejeu. Un arrêt demandé pendant le rejeu lève `StopRequested` (le
        rejeu reprend depuis son dernier checkpoint au jugement suivant)."""
        checks: dict[str, dict[str, Any]] = {}

        def check(name: str, ok: bool, detail: str, cause: str | None = None) -> bool:
            checks[name] = {"ok": ok, "detail": detail, **({"cause": cause} if cause else {})}
            return ok

        # 1. Intégrité et mesure du candidat.
        try:
            candidate, candidate_repro, candidate_model = self._measure_twice(
                lambda: load_model_from_artifact(self.store, candidate_artifact, self.world)
            )
        except Exception as exc:  # checkpoint absent, altéré, autre dataset, autre tokenizer
            check("candidate_integrity", False, f"{exc.__class__.__name__}: {exc}")
            return JudgeResult(
                JudgeDecision.REJECT.value, [f"checkpoint candidat invalide : {exc}"], checks
            )
        check("candidate_integrity", True, "hash, dataset et tokenizer vérifiés")

        # 2. Contamination.
        train_ids = {c.candidate_id for c in self.world.dataset.splits[Split.TRAIN]}
        forbidden = {
            c.candidate_id for s in (Split.VALIDATION, Split.TEST) for c in self.world.dataset.splits[s]
        }
        declared = set(training_candidate_ids)
        leaked = sorted(declared & forbidden)
        unknown = sorted(declared - train_ids - forbidden)
        check(
            "no_contamination",
            bool(declared) and not leaked and not unknown,
            "tous les documents d'entraînement appartiennent au split train"
            if declared and not leaked and not unknown
            else "aucun document déclaré"
            if not declared
            else f"documents d'évaluation dans l'entraînement : {leaked[:3]} ; inconnus : {unknown[:3]}",
        )

        # 3. Reproductibilité et sincérité des mesures déclarées.
        check(
            "candidate_reproducible",
            candidate_repro,
            "deux mesures indépendantes identiques"
            if candidate_repro
            else "deux mesures du même checkpoint diffèrent",
        )
        check(
            "claimed_metrics_match",
            _same_scores(candidate, claimed_reports),
            "mesures déclarées = mesures du Judge"
            if _same_scores(candidate, claimed_reports)
            else "les mesures déclarées par l'exécuteur ne sont pas celles du Judge",
        )

        # 4. Référence : champion (relu et re-mesuré, puis comparé aux mesures
        # enregistrées à sa promotion — ce qui vérifie aussi sa reproductibilité)
        # ou initialisation (graine du plan).
        try:
            if baseline_artifact is not None:
                baseline_model = load_model_from_artifact(self.store, baseline_artifact, self.world)
            else:
                baseline_model = initial_model(self.world, baseline_seed)
            baseline = measure(baseline_model, self.world)
        except Exception as exc:
            check("baseline_integrity", False, f"{exc.__class__.__name__}: {exc}")
            return JudgeResult(
                JudgeDecision.REJECT.value,
                [f"référence illisible : {exc}"],
                checks,
                candidate_reports=candidate,
            )
        check("baseline_integrity", True, "référence relue et mesurée")
        if baseline_stored_reports is not None:
            same = _same_scores(baseline, baseline_stored_reports)
            check(
                "baseline_unchanged",
                same,
                "mesures du champion identiques à celles du registre"
                if same
                else "les mesures du champion au registre ne correspondent plus au checkpoint",
            )
        same_weights = baseline[PRIMARY].model_fingerprint == candidate[PRIMARY].model_fingerprint
        check(
            "candidate_differs",
            not same_weights,
            "poids différents de la référence" if not same_weights else "poids identiques à la référence",
        )
        del candidate_model, baseline_model

        # 5. Provenance : données déclarées = données de la recette ; rejeu.
        if plan is not None:
            _, expected_ids = self.world.token_stream(
                plan.recipe.mix, plan.recipe.tokens, seed=plan.recipe.seed
            )
            same_data = sorted(declared) == expected_ids and len(declared) == len(training_candidate_ids)
            check(
                "declared_data_matches_recipe",
                same_data,
                "documents déclarés = documents tirés par la recette"
                if same_data
                else "les documents déclarés ne sont pas ceux que la recette tire du split train",
            )
            if replay_dir is not None:
                self._replay(plan, candidate_artifact, replay_dir, stop, stop_file, check)

        # 5. Statistique.
        verdict = NeuralJudge(
            primary=PRIMARY, max_guard_regression_bpb=self.max_guard_regression_bpb
        ).judge(baseline, candidate)
        failed = [f"{name} : {c['detail']}" for name, c in checks.items() if not c["ok"]]
        decision = JudgeDecision.REJECT.value if failed else verdict.decision.value
        return JudgeResult(
            decision=decision,
            reasons=failed + list(verdict.reasons),
            checks=checks,
            comparisons=[c.to_dict() for c in verdict.comparisons],
            candidate_reports=candidate,
            baseline_reports=baseline,
        )
