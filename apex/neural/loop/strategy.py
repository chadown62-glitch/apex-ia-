"""UPDATE STRATEGY : choisir l'expérience suivante à partir de la faiblesse
observée, de l'analyse du dernier échec et de la mémoire.

Point de départ :
- sans champion : la recette d'amorçage fournie par l'opérateur (depuis
  l'initialisation) ;
- avec champion et une faiblesse de domaine : `target_only` (entraîner le
  champion sur le seul domaine faible) — sauf si la mémoire dit mieux (voir
  plus bas).

Règles d'adaptation (cause ÉTABLIE du dernier échec -> UNE modification) :
- PROTECTED_REGRESSION -> augmenter la part des domaines dégradés dans le
  mélange au palier suivant (`protect_levels`) — contrainte de diversité ;
- NO_SIGNIFICANT_EFFECT / NO_CHANGE -> doubler le budget de pas (dans le budget) ;
- TARGET_REGRESSION / DIVERGED -> diviser le taux d'apprentissage par deux ;
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

from apex.neural.loop.failure import INTEGRITY_CAUSES, FailureCause
from apex.neural.loop.journal import ExperimentJournal, ExperimentRecord
from apex.neural.loop.observe import Observation
from apex.neural.loop.plan import DIRECTION_DECREASE, LoopHypothesis, Recipe, Weakness, plan_fingerprint
from apex.neural.loop.world import PRIMARY, World

BOOTSTRAP = "bootstrap"
TARGET_ONLY = "target_only"
PROTECT_DOMAINS = "protect_domains"
MORE_STEPS = "more_steps"
LOWER_LR = "lower_lr"
RETRY = "retry"

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
    max_retries: int = 1

    def reference(self, obs: Observation, world: World) -> Recipe:
        champion_recipe = (obs.champion.recipe if obs.champion else None) or {}
        mix = dict(champion_recipe.get("mix") or dict.fromkeys(world.spec.domains, 1.0))
        return Recipe(
            init="champion",
            mix=mix,
            steps=self.finetune_steps,
            lr=self.finetune_lr,
            tokens=self.finetune_tokens,
            seed=world.spec.seed,
        )

    def _hypothesis(self, world: World, weakness: Weakness, strategy: str, change: str) -> LoopHypothesis:
        return LoopHypothesis(
            weakness=weakness,
            claim=(
                f"{change} réduit le BPB de « {weakness.benchmark} » (actuellement {weakness.value}) "
                "sans dégrader les suites protégées"
            ),
            modification=change,
            target_benchmark=weakness.benchmark,
            direction=DIRECTION_DECREASE,
            protected=tuple(sorted(world.benchmarks)),
            falsification=(
                f"réfutée si le gain sur « {PRIMARY} » n'est pas significatif (IC 95 % du bootstrap "
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
                        self._hypothesis(world, weakness, BOOTSTRAP, change),
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
                    self._hypothesis(world, weakness, strategy, last.plan["hypothesis"]["modification"]),
                    parent,
                    Recipe.from_dict(baseline_recipe) if baseline_recipe else None,
                    last.experiment_id,
                    base,
                )
            ]
        out: list[StrategyDecision] = []
        if cause == FailureCause.PROTECTED_REGRESSION.value:
            regressed = {
                s.split(":", 1)[1]
                for s in analysis.get("regressed_suites", [])
                if s.startswith(f"{PRIMARY}:")
            }
            if regressed:
                current = domain_share(parent.mix, regressed)
                for level in self.protect_levels:
                    if level > current:
                        mix = raise_share(parent.mix, regressed, level, list(world.spec.domains))
                        change = (
                            f"porter la part des domaines dégradés {sorted(regressed)} à "
                            f"{int(level * 100)} % du mélange (contrainte de diversité) : {mix}"
                        )
                        out.append(
                            StrategyDecision(
                                PROTECT_DOMAINS,
                                self._hypothesis(world, weakness, PROTECT_DOMAINS, change),
                                replace(parent, mix=mix),
                                parent,
                                last.experiment_id,
                                [
                                    *base,
                                    f"suites protégées dégradées {analysis.get('regressed_suites')} : "
                                    "on augmente la représentation de leurs domaines",
                                ],
                            )
                        )
        elif cause in (FailureCause.NO_SIGNIFICANT_EFFECT.value, FailureCause.NO_CHANGE.value):
            steps, tokens = parent.steps * 2, parent.tokens * 2
            if steps <= self.max_steps and tokens <= self.max_tokens:
                change = (
                    f"doubler le budget d'entraînement ({parent.steps} -> {steps} pas) avec le même mélange"
                )
                out.append(
                    StrategyDecision(
                        MORE_STEPS,
                        self._hypothesis(world, weakness, MORE_STEPS, change),
                        replace(parent, steps=steps, tokens=tokens),
                        parent,
                        last.experiment_id,
                        [*base, "aucun effet mesurable : on double le budget, dans la limite autorisée"],
                    )
                )
        elif cause in (FailureCause.TARGET_REGRESSION.value, FailureCause.DIVERGED.value):
            lr = parent.lr / 2
            if lr >= self.min_lr:
                change = f"diviser le taux d'apprentissage par deux ({parent.lr} -> {lr})"
                out.append(
                    StrategyDecision(
                        LOWER_LR,
                        self._hypothesis(world, weakness, LOWER_LR, change),
                        replace(parent, lr=lr),
                        parent,
                        last.experiment_id,
                        [*base, "la cible se dégradait ou l'entraînement divergeait : pas plus petit"],
                    )
                )
        return out

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
                    self._hypothesis(world, weakness, strategy, change),
                    replace(reference, mix=mix),
                    reference,
                    None,
                    rationale,
                )
            )
        return out
