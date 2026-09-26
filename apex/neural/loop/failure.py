"""Analyse d'échec : ce que la mesure établit, et rien de plus.

Une cause est ESTABLISHED quand elle se lit directement dans une mesure du
Judge ou dans une exception typée (régression significative sur une suite
protégée, absence d'effet, contrôle d'intégrité en échec, perte non finie,
délai). Sinon elle est UNKNOWN, avec les raisons brutes — jamais inventée.

L'ATTRIBUTION à une modification n'est revendiquée que si l'expérience ne
diffère de sa référence que par UNE modification (`single_modification`) ;
avec plusieurs, elle est `confounded`. L'INTERPRÉTATION causale (« oubli des
domaines non entraînés », etc.) est séparée et étiquetée INFERRED : c'est une
lecture plausible, pas une mesure."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from apex.errors import ArtifactIntegrityError, TimeoutExceeded
from apex.neural.dataset import LeakageError
from apex.neural.loop.execute import StopRequested
from apex.neural.loop.judge import JudgeResult
from apex.neural.loop.plan import ExperimentPlan
from apex.neural.loop.world import PRIMARY


class FailureCause(StrEnum):
    PROTECTED_REGRESSION = "protected_regression"
    TARGET_REGRESSION = "target_regression"
    NO_SIGNIFICANT_EFFECT = "no_significant_effect"
    NO_CHANGE = "no_change"
    CONTAMINATION = "contamination"
    UNDECLARED_TRAINING = "undeclared_training"
    METRIC_MISMATCH = "metric_mismatch"
    BASELINE_CHANGED = "baseline_changed"
    NOT_REPRODUCIBLE = "not_reproducible"
    CORRUPT_CHECKPOINT = "corrupt_checkpoint"
    DIVERGED = "diverged"
    TIMEOUT = "timeout"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


#: Causes qui signalent un problème d'INTÉGRITÉ du système, pas une mauvaise
#: hypothèse : la boucle s'arrête au lieu de réessayer à l'aveugle.
INTEGRITY_CAUSES = frozenset(
    {
        FailureCause.CONTAMINATION,
        FailureCause.UNDECLARED_TRAINING,
        FailureCause.METRIC_MISMATCH,
        FailureCause.BASELINE_CHANGED,
        FailureCause.NOT_REPRODUCIBLE,
        FailureCause.CORRUPT_CHECKPOINT,
    }
)

_CHECK_CAUSES = {
    "candidate_integrity": FailureCause.CORRUPT_CHECKPOINT,
    "no_contamination": FailureCause.CONTAMINATION,
    "claimed_metrics_match": FailureCause.METRIC_MISMATCH,
    "candidate_reproducible": FailureCause.NOT_REPRODUCIBLE,
    "baseline_integrity": FailureCause.CORRUPT_CHECKPOINT,
    "baseline_unchanged": FailureCause.BASELINE_CHANGED,
    "candidate_differs": FailureCause.NO_CHANGE,
    "declared_data_matches_recipe": FailureCause.UNDECLARED_TRAINING,
    "training_replayed": FailureCause.UNDECLARED_TRAINING,
}


@dataclass(frozen=True)
class FailureAnalysis:
    cause: FailureCause
    status: str  # "established" | "unknown"
    evidence: list[str]
    attribution: str  # "single_modification" | "confounded" | "none"
    implicated_modifications: list[dict[str, Any]]
    refuted: str
    reusable: list[str] = field(default_factory=list)
    interpretation: str | None = None  # toujours INFERRED
    regressed_suites: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "cause": self.cause.value,
            "interpretation_label": "INFERRED" if self.interpretation else None,
        }


def _attribution(plan: ExperimentPlan) -> tuple[str, list[dict[str, Any]]]:
    mods = plan.modifications
    return ("single_modification" if len(mods) == 1 else "confounded"), mods


def _fmt(comp: dict[str, Any]) -> str:
    low, high = comp["bpb_gain_ci"]
    return (
        f"{comp['suite']} : {comp['baseline_bpb']} -> {comp['candidate_bpb']} bpb "
        f"(gain IC 95 % [{low:.4f}, {high:.4f}], {comp['verdict']}, n={comp['n']})"
    )


def analyze_judgement(
    plan: ExperimentPlan, result: JudgeResult, *, guard_tolerance: float = 0.01
) -> FailureAnalysis | None:
    if result.accepted:
        return None
    attribution, mods = _attribution(plan)
    failed_checks = [name for name, c in result.checks.items() if not c["ok"]]
    if failed_checks:
        first = result.checks[failed_checks[0]]
        cause = (
            FailureCause(first["cause"])
            if "cause" in first
            else _CHECK_CAUSES.get(failed_checks[0], FailureCause.UNKNOWN)
        )
        return FailureAnalysis(
            cause=cause,
            status="established",
            evidence=[f"{n} : {result.checks[n]['detail']}" for n in failed_checks],
            attribution="none",
            implicated_modifications=[],
            refuted="aucune (expérience invalide, pas une réfutation)",
        )
    comps = {c["suite"]: c for c in result.comparisons}
    target = plan.hypothesis.target_benchmark
    if target not in comps or PRIMARY not in comps:
        return FailureAnalysis(
            FailureCause.UNKNOWN,
            "unknown",
            list(result.reasons),
            attribution,
            mods,
            "indéterminé : comparaisons incomplètes",
        )

    def gain_significant(c: dict[str, Any]) -> bool:
        return bool(c["verdict"] == "significant" and c["bpb_gain_ci"][0] > 0)

    def regression(c: dict[str, Any]) -> bool:
        significant = c["verdict"] == "significant" and c["bpb_gain_ci"][1] < 0
        beyond = c["suite"] != PRIMARY and c["candidate_bpb"] - c["baseline_bpb"] > guard_tolerance
        return bool(significant or beyond)

    regressed = sorted(s for s, c in comps.items() if s != target and regression(c))
    target_comp = comps[target]
    if regression(target_comp):
        return FailureAnalysis(
            FailureCause.TARGET_REGRESSION,
            "established",
            [_fmt(target_comp)],
            attribution,
            mods,
            refuted="prédiction principale : la cible s'est dégradée au lieu de s'améliorer",
            regressed_suites=[target, *regressed],
        )
    if regressed:
        reusable = []
        if gain_significant(target_comp):
            reusable.append(f"la modification améliore la cible : {_fmt(target_comp)}")
        return FailureAnalysis(
            FailureCause.PROTECTED_REGRESSION,
            "established",
            [_fmt(comps[s]) for s in regressed]
            + ([_fmt(target_comp)] if gain_significant(target_comp) else []),
            attribution,
            mods,
            refuted="partie « sans dégrader les suites protégées » de l'hypothèse",
            reusable=reusable,
            interpretation=(
                "le nouveau mélange de données a déplacé la capacité du modèle vers la cible au détriment "
                f"des suites {regressed} (oubli des données moins présentes)"
            )
            if attribution == "single_modification" and gain_significant(target_comp)
            else None,
            regressed_suites=regressed,
        )
    return FailureAnalysis(
        FailureCause.NO_SIGNIFICANT_EFFECT,
        "established",
        [_fmt(comps[PRIMARY]), _fmt(target_comp)],
        attribution,
        mods,
        refuted="prédiction d'un gain significatif : effet non distinguable du bruit",
        reusable=["aucune régression significative : la modification n'est pas nuisible à ce budget"],
    )


def analyze_exception(plan: ExperimentPlan, exc: BaseException) -> FailureAnalysis:
    attribution, mods = _attribution(plan)
    detail = f"{exc.__class__.__name__}: {exc}"
    typed: list[tuple[type[BaseException], FailureCause]] = [
        (StopRequested, FailureCause.STOPPED),
        (TimeoutExceeded, FailureCause.TIMEOUT),
        (FloatingPointError, FailureCause.DIVERGED),
        (ArtifactIntegrityError, FailureCause.CORRUPT_CHECKPOINT),
        (LeakageError, FailureCause.CONTAMINATION),
    ]
    for kind, cause in typed:
        if isinstance(exc, kind):
            return FailureAnalysis(
                cause,
                "established",
                [detail],
                attribution if cause is FailureCause.DIVERGED else "none",
                mods if cause is FailureCause.DIVERGED else [],
                refuted="aucune (l'expérience n'a pas produit de mesure)",
            )
    return FailureAnalysis(
        FailureCause.UNKNOWN,
        "unknown",
        [detail],
        "none",
        [],
        refuted="aucune (cause inconnue : rien n'est conclu)",
    )
