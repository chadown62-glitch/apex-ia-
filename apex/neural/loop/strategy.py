"""UPDATE STRATEGY : choisir l'expérience suivante à partir de la faiblesse
observée, de l'analyse du dernier échec et de la mémoire.

Point de départ :
- sans champion : la recette d'amorçage fournie par l'opérateur (depuis
  l'initialisation) ;
- avec champion et une faiblesse de domaine : `target_only` (entraîner le
  champion sur le seul domaine faible) — sauf si la mémoire dit mieux (voir
  plus bas).

Règles d'adaptation (cause ÉTABLIE du dernier échec -> UNE modification) :
- un domaine ENTRAÎNÉ régresse et l'entraînement a relu son échantillon plus de
  `max_passes` fois (fait mesuré : jetons vus / jetons tirés) -> doubler les
  données tirées d'abord (sur-apprentissage : INFERRED) ;
- PROTECTED_REGRESSION -> si tous les domaines entraînés régressent : plus de
  données, puis taux d'apprentissage divisé par deux ; sinon augmenter la part
  des domaines dégradés au palier suivant (`protect_levels`) ;
- NO_SIGNIFICANT_EFFECT / NO_CHANGE -> doubler le budget de pas (dans le budget) ;
- TARGET_REGRESSION / DIVERGED -> diviser le taux d'apprentissage par deux ;
- une modification qui n'a réduit (bootstrap apparié, candidat contre candidat précédent)
  l'oubli d'AUCUNE suite que l'expérience précédente dégradait est annulée : les candidats
  repartent de la recette précédente, ce qui écarte ce levier (plan déjà exécuté) ;
- UNKNOWN / TIMEOUT -> rejouer le même plan, au plus `max_retries` fois ;
- intégrité, arrêt -> aucune suite automatique.

Mémoire (avant une première tentative avec champion) : une stratégie de
rejeu déjà promue sert de point de départ (« cela a fonctionné dans ces
conditions ») ; un `target_only` déjà rejeté pour régression protégée n'est
pas retenté (« j'ai déjà essayé ça »). Un plan déjà exécuté (même point de
départ, même recette) n'est jamais relancé, sauf nouvelle tentative bornée
après cause inconnue."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from apex.neural.evaluation import EvalReport
from apex.neural.loop.failure import INTEGRITY_CAUSES, FailureCause
from apex.neural.loop.journal import ExperimentJournal, ExperimentRecord
from apex.neural.loop.observe import Observation
from apex.neural.loop.plan import DIRECTION_DECREASE, LoopHypothesis, Recipe, Weakness, plan_fingerprint
from apex.neural.loop.world import PRIMARY, World
from apex.stattest import StatTest, StatTestVerdict

BOOTSTRAP = "bootstrap"
TARGET_ONLY = "target_only"
PROTECT_DOMAINS = "protect_domains"
MORE_STEPS = "more_steps"
MORE_DATA = "more_data"
LOWER_LR = "lower_lr"
RETRY = "retry"
#: Expérience posée par l'opérateur (LoopController.run_directed), jamais choisie par la politique.
DIRECTED = "directed"

#: Causes sans lien établi avec l'hypothèse (panne inconnue, délai dépassé
#: sous charge) : le MÊME plan peut être rejoué, au plus `max_retries` fois.
RETRYABLE = frozenset({FailureCause.UNKNOWN.value, FailureCause.TIMEOUT.value})


@dataclass(frozen=True)
class StrategyDecision:
    strategy: str
    hypothesis: LoopHypothesis
    recipe: Recipe
    baseline_recipe: Recipe | None
    parent_experiment_id: str | None
    rationale: list[str]
    retry_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "rationale": self.rationale,
            "parent": self.parent_experiment_id,
            "retry_of": self.retry_of,
        }


def lever_effect(previous: ExperimentRecord, last: ExperimentRecord) -> dict[str, Any] | None:
    """Effet MESURÉ de la modification de `last` (par rapport à `previous`) sur l'oubli : pour
    chaque suite que `previous` dégradait, bootstrap apparié par élément entre les mesures du Judge
    des deux candidats. `improved` : oubli significativement réduit (même entièrement corrigé) ;
    `unaffected` : toujours dégradée, sans réduction significative. None si les mesures manquent
    (rien n'est présumé)."""
    before = (previous.payload("judged") or {}).get("candidate_reports") or {}
    after = (last.payload("judged") or {}).get("candidate_reports") or {}
    still = set((last.analysis or {}).get("regressed_suites", []))
    degraded = (previous.analysis or {}).get("regressed_suites", [])
    suites = [s for s in degraded if s in before and s in after]
    if not suites:
        return None
    stat = StatTest(n_bootstrap=2000, confidence_level=0.95, seed=0)
    improved, unaffected, facts = [], [], []
    for suite in suites:
        old, new = EvalReport.from_dict(before[suite]), EvalReport.from_dict(after[suite])
        old_by_id = {s.doc_id: s.value for s in old.scores}
        new_by_id = {s.doc_id: s.value for s in new.scores}
        ids = sorted(set(old_by_id) & set(new_by_id))
        gain = stat.compare_paired([new_by_id[i] for i in ids], [old_by_id[i] for i in ids])  # > 0 : mieux
        helped = gain.verdict is StatTestVerdict.SIGNIFICANT and (gain.ci_lower or 0.0) > 0
        if helped:
            improved.append(suite)
        elif suite in still:
            unaffected.append(suite)
        facts.append(
            f"{suite} {round(old.value, 4)} -> {round(new.value, 4)} "
            f"(IC du gain [{gain.ci_lower}, {gain.ci_upper}]{', réduit' if helped else ''})"
        )
    modification = last.plan.get("hypothesis", {}).get("modification")
    return {
        "improved": improved,
        "unaffected": unaffected,
        "fact": (
            f"ESTABLISHED : effet de « {modification} » ({last.experiment_id}) sur l'oubli laissé par "
            f"{previous.experiment_id} : {'; '.join(facts)}"
        ),
    }


def helpful_levers(journal: ExperimentJournal, last: ExperimentRecord) -> dict[str, set[str]]:
    """Le long de la chaîne d'expériences qui mène à `last` : stratégie -> suites dont elle a
    MESURABLEMENT réduit l'oubli (effet de chaque maillon sur son parent)."""
    out: dict[str, set[str]] = {}
    experiments = journal.experiments()
    node, seen = last, set()
    while node is not None and node.experiment_id not in seen:
        seen.add(node.experiment_id)
        parent = experiments.get(node.plan.get("parent_experiment_id") or "")
        if parent is None:
            break
        effect = lever_effect(parent, node)
        if effect is not None and effect["improved"]:
            strategy = node.plan.get("hypothesis", {}).get("strategy", "")
            out.setdefault(strategy, set()).update(effect["improved"])
        node = parent
    return out


def domain_share(mix: dict[str, float], domains: set[str]) -> float:
    total = sum(mix.values()) or 1.0
    return round(sum(mix.get(d, 0.0) for d in domains) / total, 4)


def raise_share(
    mix: dict[str, float], domains: set[str], share: float, all_domains: list[str]
) -> dict[str, float]:
    """Nouveau mélange où `domains` pèsent `share` (répartie également) et le
    reste garde ses proportions relatives."""
    rest = {d: w for d, w in mix.items() if d not in domains and w > 0}
    rest_total = sum(rest.values())
    out = {d: round(share / len(domains), 4) for d in sorted(domains)}
    if rest_total > 0:
        out.update({d: round((1 - share) * w / rest_total, 4) for d, w in rest.items()})
    elif share < 1:
        others = [d for d in all_domains if d not in domains]
        out.update({d: round((1 - share) / len(others), 4) for d in others})
    return out


@dataclass(frozen=True)
class StrategyPolicy:
    finetune_steps: int = 200
    finetune_lr: float = 1e-3
    finetune_tokens: int = 60_000
    protect_levels: tuple[float, ...] = (0.25, 0.5, 0.75)
    max_steps: int = 800
    max_tokens: int = 240_000
    min_lr: float = 1e-5
    #: Au-delà, un entraînement qui relit son échantillon est présumé sur-apprendre.
    max_passes: float = 4.0
    max_retries: int = 1
    #: Graine de la recette de référence (celle du monde par défaut) : une réplication change la
    #: graine de la référence ET de l'expérience, qui ne diffèrent toujours que d'une modification.
    seed: int | None = None

    def reference(self, obs: Observation, world: World) -> Recipe:
        champion_recipe = (obs.champion.recipe if obs.champion else None) or {}
        mix = dict(champion_recipe.get("mix") or dict.fromkeys(world.spec.domains, 1.0))
        return Recipe(
            init="champion",
            mix=mix,
            steps=self.finetune_steps,
            lr=self.finetune_lr,
            tokens=self.finetune_tokens,
            seed=world.spec.seed if self.seed is None else self.seed,
        )

    def hypothesis(self, world: World, weakness: Weakness, strategy: str, change: str) -> LoopHypothesis:
        # La suite qui décidera est fixée AVANT l'expérience (voir IndependentJudge.primary_for).
        primary = weakness.benchmark if weakness.benchmark in world.acceptance_suites else PRIMARY
        capability = world.capability_of_suite(weakness.benchmark)
        metric = world.capabilities[capability][0].contract.metric if capability else "BPB"
        return LoopHypothesis(
            weakness=weakness,
            claim=(
                f"{change} réduit le {metric} de « {weakness.benchmark} » (actuellement {weakness.value}) "
                "sans dégrader les suites protégées"
            ),
            modification=change,
            target_benchmark=weakness.benchmark,
            direction=DIRECTION_DECREASE,
            protected=tuple(sorted(world.suite_names)),
            falsification=(
                f"réfutée si le gain sur « {primary} » n'est pas significatif (IC 95 % du bootstrap "
                "apparié contenant 0) ou si une suite protégée régresse significativement ou au-delà "
                "de la tolérance"
            ),
            strategy=strategy,
        )

    def decide(
        self,
        obs: Observation,
        world: World,
        journal: ExperimentJournal,
        *,
        exhausted: set[str],
        bootstrap: Recipe | None = None,
    ) -> StrategyDecision | None:
        baseline_artifact = obs.champion.artifact if obs.champion else None
        if obs.champion is not None and not baseline_artifact:
            return None
        if obs.champion is None and bootstrap is None:
            return None
        weakness = next((w for w in obs.weaknesses if w.benchmark not in exhausted), None)
        if weakness is None or (obs.champion is not None and weakness.domain is None):
            return None
        records = [
            r
            for r in journal.experiments().values()
            if r.finished
            and r.plan.get("baseline", {}).get("artifact") == baseline_artifact
            and r.plan.get("target_benchmark") == weakness.benchmark
        ]
        records.sort(key=lambda r: r.events[0]["seq"])
        for decision in self._candidates(obs, world, journal, weakness, records, bootstrap):
            fingerprint = plan_fingerprint(world.fingerprint, baseline_artifact, decision.recipe)
            same = [r for r in records if r.plan["fingerprint"] == fingerprint]
            if not same:
                return decision
            cause = (same[-1].analysis or {}).get("cause")
            if cause in RETRYABLE and len(same) <= self.max_retries:
                return replace(
                    decision,
                    strategy=RETRY,
                    retry_of=same[-1].experiment_id,
                    rationale=[
                        *decision.rationale,
                        f"nouvelle tentative ({len(same)}/{self.max_retries}) après cause inconnue de "
                        f"{same[-1].experiment_id}",
                    ],
                )
        return None

    def _candidates(
        self,
        obs: Observation,
        world: World,
        journal: ExperimentJournal,
        weakness: Weakness,
        records: list[ExperimentRecord],
        bootstrap: Recipe | None,
    ) -> list[StrategyDecision]:
        last = records[-1] if records else None
        if last is None:
            if obs.champion is None:
                assert bootstrap is not None
                change = (
                    f"amorçage depuis l'initialisation : {bootstrap.steps} pas, mélange {bootstrap.mix}"
                )
                return [
                    StrategyDecision(
                        BOOTSTRAP,
                        self.hypothesis(world, weakness, BOOTSTRAP, change),
                        bootstrap,
                        None,
                        None,
                        [f"aucun champion : {weakness.evidence}"],
                    )
                ]
            assert weakness.domain is not None
            return self._first_attempts(
                world, journal, weakness, weakness.domain, self.reference(obs, world)
            )
        analysis = last.analysis or {}
        cause = analysis.get("cause")
        parent = Recipe.from_dict(last.plan["recipe"])
        base = [f"dernier échec {last.experiment_id} : cause {cause} ({analysis.get('status')})"]
        stop = {c.value for c in INTEGRITY_CAUSES} | {FailureCause.STOPPED.value}
        if cause is None or cause in stop:
            return []
        strategy = last.plan["hypothesis"]["strategy"]
        if cause in RETRYABLE:
            baseline_recipe = last.plan["baseline_recipe"]
            return [
                StrategyDecision(
                    RETRY,
                    self.hypothesis(world, weakness, strategy, last.plan["hypothesis"]["modification"]),
                    parent,
                    Recipe.from_dict(baseline_recipe) if baseline_recipe else None,
                    last.experiment_id,
                    base,
                )
            ]
        out: list[StrategyDecision] = []
        origin = last
        regressed_suites = list(analysis.get("regressed_suites", []))
        spent: str | None = None  # levier dont l'effet mesuré interdit la répétition
        if cause in (FailureCause.PROTECTED_REGRESSION.value, FailureCause.TARGET_REGRESSION.value):
            previous = journal.experiments().get(last.plan.get("parent_experiment_id") or "")
            effect = lever_effect(previous, last) if previous is not None else None
            if previous is not None and effect is not None and effect["unaffected"]:
                base.append(effect["fact"])
                if not effect["improved"]:
                    # Sans effet sur aucune suite : modification annulée, autre levier.
                    origin, parent = previous, Recipe.from_dict(previous.plan["recipe"])
                    base.append("INFERRED : levier sans effet — annulé")
                else:
                    # X s'améliore, Y reste dégradé : on garde le levier (utile à X) et on traite Y
                    # autrement (répéter le même réglage ne l'a pas aidé).
                    spent = strategy if strategy in (LOWER_LR, MORE_DATA, MORE_STEPS) else None
                    regressed_suites = effect["unaffected"]
                    base.append(
                        f"INFERRED : « {strategy} » aide {effect['improved']} mais pas "
                        f"{effect['unaffected']} — autre levier pour ces dernières"
                    )
        regressed = {d for d in (world.suite_domain(s) for s in regressed_suites) if d}
        # Un domaine sans suite d'évaluation (ex. solutions des tâches train) ne peut pas régresser :
        # il ne compte pas parmi les domaines entraînés à comparer aux domaines dégradés.
        evaluated = {world.suite_domain(s) for s in world.suite_names}
        trained = {d for d, w in parent.mix.items() if w > 0 and d in evaluated}
        seen = (last.payload("executed") or {}).get("tokens_seen") or 0
        passes = seen / parent.tokens if parent.tokens else 0.0
        overfit = (
            cause in (FailureCause.PROTECTED_REGRESSION.value, FailureCause.TARGET_REGRESSION.value)
            and bool(trained & regressed)
            and passes > self.max_passes
        )

        def more_data(facts: str) -> None:
            tokens = parent.tokens * 2
            if tokens <= self.max_tokens:
                change = f"doubler les données tirées ({parent.tokens} -> {tokens} tokens), même mélange"
                out.append(
                    StrategyDecision(
                        MORE_DATA,
                        self.hypothesis(world, weakness, MORE_DATA, change),
                        replace(parent, tokens=tokens),
                        parent,
                        origin.experiment_id,
                        [*base, facts, "INFERRED : sur-apprentissage d'un échantillon trop petit"],
                    )
                )

        def lower_lr(facts: str) -> None:
            lr = parent.lr / 2
            if lr >= self.min_lr:
                change = f"diviser le taux d'apprentissage par deux ({parent.lr} -> {lr})"
                out.append(
                    StrategyDecision(
                        LOWER_LR,
                        self.hypothesis(world, weakness, LOWER_LR, change),
                        replace(parent, lr=lr),
                        parent,
                        origin.experiment_id,
                        [*base, facts],
                    )
                )

        # Mémoire mesurée : un levier qui a déjà réduit l'oubli de CES suites est proposé en premier,
        # même hors des heuristiques par défaut.
        helpful = {
            s: suites & set(regressed_suites)
            for s, suites in helpful_levers(journal, last).items()
            if suites & set(regressed_suites)
        }
        if LOWER_LR in helpful and not (trained and trained <= regressed):
            lower_lr(
                f"ESTABLISHED : diviser le taux a déjà réduit l'oubli de {sorted(helpful[LOWER_LR])} "
                "dans cette chaîne d'expériences"
            )
        if overfit:
            more_data(
                f"ESTABLISHED : {seen} jetons vus pour {parent.tokens} tirés ({passes:.1f} passages, "
                f"seuil {self.max_passes}) ; domaines entraînés dégradés {sorted(trained & regressed)}"
            )
        if cause == FailureCause.PROTECTED_REGRESSION.value:
            if trained and trained <= regressed:
                # Même les domaines ENTRAÎNÉS régressent : ce n'est pas un oubli par manque de
                # diversité (augmenter leur part n'a pas de sens). Une modification à la fois :
                # plus de données, puis lr.
                facts = f"domaines entraînés {sorted(trained)} eux-mêmes dégradés"
                if not overfit:
                    more_data(facts)
                lower_lr(f"{facts} ; INFERRED : pas d'apprentissage trop grand pour un affinage")
            # Les domaines entraînés sont déjà dans le mélange : on protège par le REJEU des autres
            # domaines dégradés (s'il n'y en a pas, tous les domaines dégradés).
            replayed = (regressed - trained) or regressed
            if replayed:
                current = domain_share(parent.mix, replayed)
                for level in self.protect_levels:
                    if level > current:
                        mix = raise_share(parent.mix, replayed, level, list(world.spec.domains))
                        change = (
                            f"porter la part des domaines dégradés {sorted(replayed)} à "
                            f"{int(level * 100)} % du mélange (contrainte de diversité) : {mix}"
                        )
                        out.append(
                            StrategyDecision(
                                PROTECT_DOMAINS,
                                self.hypothesis(world, weakness, PROTECT_DOMAINS, change),
                                replace(parent, mix=mix),
                                parent,
                                origin.experiment_id,
                                [
                                    *base,
                                    f"suites protégées dégradées {regressed_suites} : "
                                    "on augmente la représentation de leurs domaines",
                                ],
                            )
                        )
        elif cause in (
            FailureCause.NO_SIGNIFICANT_EFFECT.value,
            FailureCause.NO_CHANGE.value,
            FailureCause.NOT_REPLICATED.value,
        ):
            # UNE modification : le nombre de pas (plus d'époques sur les mêmes données). Doubler
            # aussi les tokens en ferait deux, et le plan serait refusé (attribution impossible).
            steps = parent.steps * 2
            if steps <= self.max_steps:
                change = f"doubler le nombre de pas ({parent.steps} -> {steps}) avec le même mélange"
                out.append(
                    StrategyDecision(
                        MORE_STEPS,
                        self.hypothesis(world, weakness, MORE_STEPS, change),
                        replace(parent, steps=steps),
                        parent,
                        origin.experiment_id,
                        [*base, "aucun effet mesurable : on double le budget, dans la limite autorisée"],
                    )
                )
        elif cause in (FailureCause.TARGET_REGRESSION.value, FailureCause.DIVERGED.value):
            lower_lr("la cible se dégradait ou l'entraînement divergeait : pas plus petit")
        return [d for d in out if d.strategy != spent]

    def _first_attempts(
        self, world: World, journal: ExperimentJournal, weakness: Weakness, target: str, reference: Recipe
    ) -> list[StrategyDecision]:
        history = [r for r in journal.experiments().values() if r.finished]
        others = {d for d in world.spec.domains if d != target}
        rationale: list[str] = [f"faiblesse observée : {weakness.evidence}"]
        levels: list[float] = []
        succeeded = [
            r
            for r in history
            if r.promoted
            and r.plan.get("hypothesis", {}).get("strategy") in (PROTECT_DOMAINS, TARGET_ONLY)
            and r.plan.get("baseline", {}).get("artifact")
        ]
        failed_naive = [
            r
            for r in history
            if r.plan.get("hypothesis", {}).get("strategy") == TARGET_ONLY
            and (r.analysis or {}).get("cause") == FailureCause.PROTECTED_REGRESSION.value
        ]
        if succeeded:
            best = succeeded[-1]
            best_target = best.plan["hypothesis"]["weakness"]["domain"]
            best_others = {d for d in world.spec.domains if d != best_target}
            level = domain_share(best.plan["recipe"]["mix"], best_others)
            levels.append(level)
            rationale.append(
                f"mémoire : {best.experiment_id} a été promu avec {int(level * 100)} % de rejeu"
            )
        if failed_naive:
            rationale.append(
                f"mémoire : {failed_naive[-1].experiment_id} (seul domaine cible) a été rejeté pour "
                "régression protégée — non retenté"
            )
        elif 0.0 not in levels:
            levels.insert(0, 0.0)
        levels += [lv for lv in self.protect_levels if lv not in levels]
        out = []
        for level in levels:
            mix = (
                raise_share({target: 1.0}, others, level, list(world.spec.domains))
                if level
                else {target: 1.0}
            )
            if level == 0.0:
                strategy = TARGET_ONLY
                change = (
                    f"entraîner le champion sur le seul domaine « {target} » (augmenter sa représentation)"
                )
            else:
                strategy = PROTECT_DOMAINS
                change = (
                    f"entraîner le champion sur « {target} » avec {int(level * 100)} % de rejeu : {mix}"
                )
            out.append(
                StrategyDecision(
                    strategy,
                    self.hypothesis(world, weakness, strategy, change),
                    replace(reference, mix=mix),
                    reference,
                    None,
                    rationale,
                )
            )
        # Une décision identique à la référence n'est pas une expérience (le plan serait refusé) :
        # c'est le cas quand le mélange du champion est déjà celui d'une tentative proposée.
        kept = [d for d in out if d.baseline_recipe is None or d.recipe.diff(d.baseline_recipe)]
        if len(kept) < len(out):
            steps = min(reference.steps * 2, self.max_steps)
            if steps != reference.steps:
                change = (
                    f"le mélange du champion {reference.mix} est déjà une tentative proposée : "
                    f"prolonger l'entraînement ({reference.steps} -> {steps} pas) sur ce mélange"
                )
                kept.insert(
                    0,
                    StrategyDecision(
                        MORE_STEPS,
                        self.hypothesis(world, weakness, MORE_STEPS, change),
                        replace(reference, steps=steps),
                        reference,
                        None,
                        [*rationale, "mélange déjà proposé : on augmente le budget de pas"],
                    ),
                )
        return kept
