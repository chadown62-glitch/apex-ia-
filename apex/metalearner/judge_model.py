"""Vocabulaire fermé du Judge System — passe « Strict Judge System +
Gauntlet Learning Loop » (N+8). Dataclasses PURES, aucune DB (même
séparation que `population_model.py`/`learning_session_model.py`).

**Principe central** (§2 de la mission) : « A solution does not pass
because it looks good. A solution passes because it survives hostile
evaluation. » Chaque `JudgeResult` sépare explicitement `decision` (le
verdict), `score` (une mesure continue, jamais suffisante seule) et
`confidence` (la force de la preuve derrière `score`) — un score élevé à
faible confiance n'équivaut JAMAIS à une preuve forte (§10).

**`JudgeDimension` couvre le vocabulaire COMPLET de la mission** (§5), mais
seule une PARTIE a un juge réel aujourd'hui (voir `apex.metalearner.judges`
pour la liste exacte) — les autres (`EDGE_CASES`/`QUALITY`/`ADVERSARIAL`)
restent délibérément non instanciées, exactement le même honnête
« `PLANNED`, jamais un juge inventé » que `JudgeTierLevel.TIER_1`/`TIER_2`
(`apex.judge.model`, Phase 9) : l'architecture (cet enum, `JudgeResult`,
l'agrégateur) permet de les ajouter SANS modifier l'agrégateur ni
l'orchestrateur, mais N+8 n'instancie que ce qui a une preuve objective
réellement disponible aujourd'hui.

**Ne remplace ni `apex.oracles.model.OracleVerdict` ni `apex.judge.model.
JudgeVerdict`** : `CorrectnessJudge` (`apex.metalearner.judges`) ENVELOPPE
`OracleVerdict`, ne réimplémente aucune logique de correction — ce module
ajoute seulement la couche « plusieurs dimensions, preuves structurées,
véto, agrégation déterministe » qu'aucun des deux modules existants ne
couvre (`JudgeTier` ne porte qu'UN verdict/UN palier, jamais plusieurs
dimensions indépendantes)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apex.db.models import GauntletResultRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JudgeDimension(str, Enum):
    #: Enveloppe `OracleVerdict` — la tâche a-t-elle été résolue ? Juge réel
    #: (`CorrectnessJudge`).
    CORRECTNESS = "correctness"
    #: Cohérence entre exécutions RÉPÉTÉES du même couple (tâche, Genome) —
    #: un candidat non-déterministe n'est jamais un candidat robuste, même
    #: s'il a réussi une fois. Juge réel (`RobustnessJudge`).
    ROBUSTNESS = "robustness"
    #: Coût réel (temps/ressources) rapporté au budget RÉELLEMENT appliqué
    #: (`ResourceLimits`) — jamais une durée absolue arbitraire. Juge réel
    #: (`EfficiencyJudge`).
    EFFICIENCY = "efficiency"
    #: Tentative de contournement du sandbox (accès réseau refusé,
    #: permission refusée) — signal RÉEL déjà produit par
    #: `apex.sandbox.model.ExecutionStatus`, jamais un scan de sécurité
    #: inventé. Juge réel (`SecurityJudge`).
    SECURITY = "security"
    #: Le candidat généralise-t-il au-delà des tâches TRAIN vues pendant
    #: l'évolution — le contrôle HELDOUT existant (§13, N+6), désormais
    #: exposé comme jugement structuré plutôt qu'un booléen interne. Juge
    #: réel (`GeneralizationJudge`).
    GENERALIZATION = "generalization"
    #: Comparaison DIRECTE avec le champion courant — une moyenne
    #: supérieure ne doit jamais masquer une régression majeure sur une
    #: composante isolée (§12). Juge réel (`RegressionJudge`).
    REGRESSION = "regression"
    #: PLANNED — nécessiterait une génération de tâches délibérément
    #: limites/dégénérées au-delà de ce que `apex.taskgen` produit
    #: aujourd'hui (NORMAL/TARGETED_WEAKNESS/CURRICULUM_INTERMEDIATE/
    #: REGRESSION, aucun mode "edge case" dédié) — aucun juge n'est
    #: instancié pour cette dimension.
    EDGE_CASES = "edge_cases"
    #: PLANNED — chevaucherait `CORRECTNESS` sans preuve supplémentaire
    #: distincte tant qu'aucune mesure de qualité (style, lisibilité, ...)
    #: n'existe réellement (même refus explicite que `population_fitness.
    #: py` pour un `latency_penalty` séparé d'`elapsed_s` : ne pas inventer
    #: une distinction que les données ne portent pas).
    QUALITY = "quality"
    #: PLANNED — la mission distingue explicitement GENERALIZATION (HELDOUT,
    #: réel) d'un futur générateur de cas ADVERSARIAL dédié (perturbation,
    #: mutation-based tests, §7/§26) : ce dernier n'existe pas encore,
    #: jamais confondu avec le contrôle HELDOUT déjà réel.
    ADVERSARIAL = "adversarial"


#: Dimensions couvertes par un juge RÉEL dans cette passe — utilisé par les
#: tests/documentation pour ne jamais prétendre qu'une dimension PLANNED a
#: un jugement réel.
REAL_DIMENSIONS = frozenset(
    {
        JudgeDimension.CORRECTNESS, JudgeDimension.ROBUSTNESS, JudgeDimension.EFFICIENCY,
        JudgeDimension.SECURITY, JudgeDimension.GENERALIZATION, JudgeDimension.REGRESSION,
    }
)
PLANNED_DIMENSIONS = frozenset(JudgeDimension) - REAL_DIMENSIONS


class JudgeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    #: Un finding CRITICAL est un VÉTO (§8) — bloque la décision globale
    #: quel que soit le score des autres dimensions, jamais moyenné.
    CRITICAL = "critical"


class JudgeDecision(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    #: Preuve insuffisante pour trancher — jamais silencieusement traité
    #: comme un PASS (§9/§10/§23). Utilisé aussi bien au niveau d'UN juge
    #: qu'au niveau de la décision AGRÉGÉE (même vocabulaire, jamais un
    #: second nom pour le même concept).
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class JudgeEvidence:
    """Une référence CITABLE, jamais un texte narratif seul (§10) — `kind`
    identifie le TYPE de preuve (`"eval_run"`, `"sandbox_result"`,
    `"repeated_execution"`, `"holdout_run"`, `"champion_comparison"`),
    `reference` un identifiant réel retrouvable (ex. `EvalRun.id`,
    `PopulationMember.id`)."""

    kind: str
    reference: str
    detail: str = ""


@dataclass(frozen=True)
class JudgeFinding:
    dimension: JudgeDimension
    severity: JudgeSeverity
    summary: str
    detail: str = ""

    @property
    def is_veto(self) -> bool:
        return self.severity is JudgeSeverity.CRITICAL


@dataclass(frozen=True)
class JudgeResult:
    """Le jugement d'UN juge sur UNE dimension. `judge_id`/`judge_version`
    (§17) rendent chaque jugement reproductible/interprétable même après une
    future évolution du protocole — une future version n'invalide jamais un
    résultat déjà persisté, elle documente seulement lequel l'a produit."""

    judge_id: str
    judge_version: str
    dimension: JudgeDimension
    decision: JudgeDecision
    score: float | None
    confidence: float | None
    rationale: str
    findings: tuple[JudgeFinding, ...] = field(default_factory=tuple)
    evidence: tuple[JudgeEvidence, ...] = field(default_factory=tuple)

    @property
    def veto(self) -> bool:
        return any(f.is_veto for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        """Projection JSON-sérialisable — utilisée pour la persistance
        (`GauntletResultRow.judge_results`, colonne JSON) ET pour l'API,
        jamais deux logiques de sérialisation distinctes."""
        return {
            "judge_id": self.judge_id, "judge_version": self.judge_version,
            "dimension": self.dimension.value, "decision": self.decision.value,
            "score": self.score, "confidence": self.confidence, "rationale": self.rationale,
            "veto": self.veto,
            "findings": [
                {
                    "dimension": f.dimension.value, "severity": f.severity.value,
                    "summary": f.summary, "detail": f.detail,
                }
                for f in self.findings
            ],
            "evidence": [
                {"kind": e.kind, "reference": e.reference, "detail": e.detail} for e in self.evidence
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JudgeResult:
        return cls(
            judge_id=data["judge_id"], judge_version=data["judge_version"],
            dimension=JudgeDimension(data["dimension"]), decision=JudgeDecision(data["decision"]),
            score=data["score"], confidence=data["confidence"], rationale=data["rationale"],
            findings=tuple(
                JudgeFinding(
                    dimension=JudgeDimension(f["dimension"]), severity=JudgeSeverity(f["severity"]),
                    summary=f["summary"], detail=f.get("detail", ""),
                )
                for f in data.get("findings", ())
            ),
            evidence=tuple(
                JudgeEvidence(kind=e["kind"], reference=e["reference"], detail=e.get("detail", ""))
                for e in data.get("evidence", ())
            ),
        )


#: Version du PROTOCOLE d'agrégation (§17) — une constante de CODE, jamais
#: une valeur admin-configurable (changer le protocole change le
#: comportement réel de l'agrégateur ; un simple réglage de config ne
#: pourrait jamais refléter honnêtement une telle différence). Incrémentée
#: SEULEMENT si `apex.metalearner.gauntlet._aggregate` change de règle.
GAUNTLET_PROTOCOL_VERSION = "1"


class GauntletStage(str, Enum):
    #: Juges qui ne coûtent AUCUNE exécution supplémentaire — réutilisent
    #: l'unique expérience déjà réelle de ce membre (`CORRECTNESS`/
    #: `EFFICIENCY`/`SECURITY`/`REGRESSION`). Calculé pour CHAQUE membre.
    CHEAP = "cheap"
    #: Ajoute les juges qui exigent une exécution RÉELLE supplémentaire
    #: (`ROBUSTNESS` = ré-exécution, `GENERALIZATION` = HELDOUT) — calculé
    #: SEULEMENT pour le candidat à la promotion CHAMPION (même profil de
    #: coût que le contrôle HELDOUT déjà existant en N+6, désormais enrichi
    #: plutôt que dupliqué).
    FULL = "full"


@dataclass(frozen=True)
class GauntletResult:
    """Décision AGRÉGÉE, déterministe, sur UN candidat — jamais une
    moyenne qui masquerait un véto (§8/§11)."""

    id: str
    specialty: str
    generation: int
    member_id: str
    stage: GauntletStage
    protocol_version: str
    decision: JudgeDecision
    overall_score: float | None
    overall_confidence: float | None
    veto: bool
    veto_dimension: JudgeDimension | None
    disagreement: bool
    stopped_after_critical_failure: bool
    judge_results: tuple[JudgeResult, ...]
    compared_champion_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        specialty: str,
        generation: int,
        member_id: str,
        stage: GauntletStage,
        decision: JudgeDecision,
        overall_score: float | None,
        overall_confidence: float | None,
        veto: bool,
        veto_dimension: JudgeDimension | None,
        disagreement: bool,
        stopped_after_critical_failure: bool,
        judge_results: tuple[JudgeResult, ...],
        compared_champion_id: str | None = None,
    ) -> GauntletResult:
        return cls(
            id=str(uuid.uuid4()), specialty=specialty, generation=generation, member_id=member_id,
            stage=stage, protocol_version=GAUNTLET_PROTOCOL_VERSION, decision=decision,
            overall_score=overall_score, overall_confidence=overall_confidence, veto=veto,
            veto_dimension=veto_dimension, disagreement=disagreement,
            stopped_after_critical_failure=stopped_after_critical_failure, judge_results=judge_results,
            compared_champion_id=compared_champion_id,
        )

    @classmethod
    def from_row(cls, row: "GauntletResultRow") -> GauntletResult:
        return cls(
            id=row.id, specialty=row.specialty, generation=row.generation, member_id=row.member_id,
            stage=GauntletStage(row.stage), protocol_version=row.protocol_version,
            decision=JudgeDecision(row.decision), overall_score=row.overall_score,
            overall_confidence=row.overall_confidence, veto=row.veto,
            veto_dimension=JudgeDimension(row.veto_dimension) if row.veto_dimension else None,
            disagreement=row.disagreement,
            stopped_after_critical_failure=row.stopped_after_critical_failure,
            judge_results=tuple(JudgeResult.from_dict(d) for d in row.judge_results),
            compared_champion_id=row.compared_champion_id, created_at=row.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "specialty": self.specialty, "generation": self.generation,
            "member_id": self.member_id, "stage": self.stage.value,
            "protocol_version": self.protocol_version, "decision": self.decision.value,
            "overall_score": self.overall_score, "overall_confidence": self.overall_confidence,
            "veto": self.veto, "veto_dimension": self.veto_dimension.value if self.veto_dimension else None,
            "disagreement": self.disagreement,
            "stopped_after_critical_failure": self.stopped_after_critical_failure,
            "judge_results": [jr.to_dict() for jr in self.judge_results],
            "compared_champion_id": self.compared_champion_id,
            "created_at": self.created_at.isoformat(),
        }
