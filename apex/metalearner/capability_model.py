"""`CapabilityBenchmarkRun`/`CapabilityMeasurement`/`CapabilityDelta` —
mesure de capacité INDÉPENDANTE (passe « Adversarial Curriculum +
Capability Benchmarking », N+10, §8/§9/§10 de la mission).

**Indépendance RÉELLE, jamais un second système de jugement** : un
`CapabilityBenchmarkRun` ENVELOPPE un `apex.metalearner.judge_model.
GauntletResult` de stage `BENCHMARK` (`GauntletRunner.run_stage`, MÊME
agrégation stricte que CHEAP/FULL, N+8, inchangée) — l'indépendance vient
de la BATTERIE DE TÂCHES (fixe, HELDOUT, jamais réutilisée par le
curriculum d'entraînement — `apex.metalearner.capability_benchmark_
service`), pas d'un second mécanisme de jugement dupliqué (§12 : jamais un
second système qui referait le même travail autrement).

**Vecteur multi-dimensionnel, JAMAIS réduit à un seul nombre** (§10 de la
mission) : `CapabilityMeasurement` porte CHAQUE dimension séparément —
`CORRECTNESS`/`ROBUSTNESS`/`SECURITY`/`GENERALIZATION`/`EFFICIENCY`
viennent d'un jugement RÉEL (`apex.metalearner.judges`, INCHANGÉ) ;
`REGRESSION` est dérivée par COMPARAISON directe au dernier
`CapabilityBenchmarkRun` du champion (jamais un juge supplémentaire — la
comparaison EST la mesure, §9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apex.metalearner.judge_model import JudgeDecision, JudgeDimension

if TYPE_CHECKING:
    from apex.metalearner.judge_model import GauntletResult, JudgeResult

CAPABILITY_BENCHMARK_PROTOCOL_VERSION = "1"

#: Toutes les dimensions RÉELLEMENT mesurées par le benchmark de capacité
#: (§8 de la mission : « au minimum CORRECTNESS/ROBUSTNESS/GENERALIZATION/
#: EFFICIENCY/SECURITY/REGRESSION ») — exactement `JudgeDimension`
#: instanciée par N+8/N+9, aucune dimension supplémentaire inventée.
CAPABILITY_DIMENSIONS: tuple[JudgeDimension, ...] = (
    JudgeDimension.CORRECTNESS,
    JudgeDimension.ROBUSTNESS,
    JudgeDimension.GENERALIZATION,
    JudgeDimension.EFFICIENCY,
    JudgeDimension.SECURITY,
    JudgeDimension.REGRESSION,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CapabilityMeasurement:
    dimension: JudgeDimension
    decision: JudgeDecision
    score: float | None
    confidence: float | None
    evidence_reference: str | None

    @classmethod
    def from_judge_result(cls, judge_result: JudgeResult) -> CapabilityMeasurement:
        evidence_reference = judge_result.evidence[0].reference if judge_result.evidence else None
        return cls(
            dimension=judge_result.dimension, decision=judge_result.decision, score=judge_result.score,
            confidence=judge_result.confidence, evidence_reference=evidence_reference,
        )


@dataclass(frozen=True)
class CapabilityBenchmarkRun:
    """`gauntlet_result_id` référence le `GauntletResult` (stage
    `BENCHMARK`) qui a RÉELLEMENT produit ces mesures — jamais une seconde
    persistance des mêmes preuves (`apex.metalearner.gauntlet_repository`
    reste l'unique source de vérité sur le détail par juge)."""

    id: str
    specialty: str
    member_id: str
    generation: int
    genome_version_id: str
    benchmark_version: str
    gauntlet_result_id: str
    overall_decision: JudgeDecision
    overall_score: float | None
    overall_confidence: float | None
    measurements: tuple[CapabilityMeasurement, ...]
    compared_champion_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)

    def measurement_for(self, dimension: JudgeDimension) -> CapabilityMeasurement | None:
        for measurement in self.measurements:
            if measurement.dimension is dimension:
                return measurement
        return None

    @classmethod
    def from_gauntlet_result(
        cls, result: GauntletResult, *, genome_version_id: str, benchmark_version: str,
    ) -> CapabilityBenchmarkRun:
        """`id` réutilise DIRECTEMENT `result.id` — jamais un nouvel
        identifiant généré à chaque lecture (§11 de la mission N+9,
        réappliqué ici : un identifiant doit rester STABLE d'une lecture à
        l'autre de la MÊME ligne `gauntlet_results`, jamais aléatoire)."""
        return cls(
            id=result.id, specialty=result.specialty, member_id=result.member_id,
            generation=result.generation, genome_version_id=genome_version_id,
            benchmark_version=benchmark_version, gauntlet_result_id=result.id,
            overall_decision=result.decision, overall_score=result.overall_score,
            overall_confidence=result.overall_confidence,
            measurements=tuple(CapabilityMeasurement.from_judge_result(jr) for jr in result.judge_results),
            compared_champion_id=result.compared_champion_id, created_at=result.created_at,
        )


@dataclass(frozen=True)
class CapabilityDelta:
    """§9 de la mission : « une moyenne globale ne doit jamais masquer une
    régression critique » — `dimension_deltas` reste un VECTEUR, jamais
    moyenné ; `critical_regression` est calculé séparément (une dimension
    qui régresse d'un `decision` PASS/non-CRITIQUE vers REJECT/CRITIQUE),
    et bloque la promotion INDÉPENDAMMENT du signe des autres dimensions."""

    specialty: str
    candidate_run_id: str
    reference_kind: str  # "champion" | "historical_best"
    reference_run_id: str | None
    dimension_deltas: dict[JudgeDimension, float | None]
    critical_regression: bool
    regressed_dimensions: tuple[JudgeDimension, ...]
    missing_reference_dimensions: tuple[JudgeDimension, ...]


@dataclass(frozen=True)
class PromotionRuleResult:
    """§13 de la mission — jamais un `PROMOTE` forcé : `decision` réutilise
    le vocabulaire déjà réel `JudgeDecision` (`PASS` = capacité confirmée
    non régressée, `REJECT` = régression critique/capacité non améliorée,
    `INCONCLUSIVE` = preuve insuffisante, jamais un `PASS` par défaut)."""

    decision: JudgeDecision
    reason: str
    blocking_dimension: JudgeDimension | None = None
