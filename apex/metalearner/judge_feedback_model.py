"""Vocabulaire fermé du pont Judge -> LearningFeedback (passe « Judge
Feedback -> Weakness -> Curriculum -> Adaptive Evolution », N+9).
Dataclasses PURES, aucune DB (même séparation que `judge_model.py`).

**Ce module ne réimplémente RIEN de N+8** : `JudgeLearningSignal` ENVELOPPE
un `apex.metalearner.judge_model.JudgeResult` déjà réel (`judge_id`/
`dimension`/`decision`/`score`/`confidence`/`findings`/`evidence` viennent
tous de là) — il ajoute SEULEMENT la couche « qu'est-ce que ce jugement dit
sur le CANDIDAT, par opposition à l'ÉVALUATION elle-même » (§11 de la
mission : « Docker indisponible ≠ candidate mauvais »).

**`SignalCategory` — distinction centrale exigée par la mission (§2/§3)** :
    SUCCESS          : `decision=PASS` — jamais autre chose.
    WEAKNESS          : `decision=REJECT`, non-CRITICAL, preuve imputable au
                        CANDIDAT (§7 : exploitable pour cibler la mutation).
    FAILURE           : l'ÉVALUATION elle-même a échoué (infrastructure) —
                        jamais confondu avec une faiblesse du candidat.
    CRITICAL_FAILURE  : `decision=REJECT` avec un finding CRITICAL (véto,
                        N+8) — jamais réduit à une simple pénalité (§2 de la
                        mission N+9 : « Ne transforme jamais un finding
                        critique en simple petite pénalité »).
    INCONCLUSIVE      : preuve insuffisante OU juge en panne — JAMAIS un
                        signal négatif artificiel (§3).

**`FailureCategory` — la distinction du §11**, orthogonale à
`SignalCategory` (répond à « POURQUOI », `SignalCategory` répond à « QUOI
FAIRE DE CE SIGNAL ») :
    CANDIDATE_FAILURE          : le candidat a réellement échoué la tâche.
    JUDGE_FAILURE              : le juge lui-même a levé une exception.
    INSUFFICIENT_EVIDENCE      : l'exécution nécessaire n'a pas eu lieu (ex.
                                  robustesse/généralisation non testées).
    INFRASTRUCTURE_FAILURE     : l'exécution a échoué pour une raison
                                  indépendante du candidat (`OracleVerdict.
                                  ERROR`).
    POLICY_REJECTION           : bloqué par une contrainte administrative
                                  (réservé — aucun déclencheur réel
                                  aujourd'hui, voir `apex.metalearner.
                                  judge_feedback_service`).
    GENUINE_CAPABILITY_WEAKNESS: le candidat a RÉELLEMENT échoué/régressé
                                  sur cette dimension (véto ou non).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

from apex.metalearner.judge_model import JudgeDecision, JudgeDimension, JudgeSeverity
from apex.taskgen.model import GenerationMode

if TYPE_CHECKING:
    from apex.db.models import JudgeLearningSignalRow
    from apex.taskgen.model import AdversarialKind, EdgeCaseKind, GeneralizationVariant


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SignalCategory(str, Enum):
    SUCCESS = "success"
    WEAKNESS = "weakness"
    FAILURE = "failure"
    CRITICAL_FAILURE = "critical_failure"
    INCONCLUSIVE = "inconclusive"


class FailureCategory(str, Enum):
    CANDIDATE_FAILURE = "candidate_failure"
    JUDGE_FAILURE = "judge_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    POLICY_REJECTION = "policy_rejection"
    GENUINE_CAPABILITY_WEAKNESS = "genuine_capability_weakness"


class ImprovementStatus(str, Enum):
    """§10 de la mission : « la correction survit-elle ? le problème
    revient-il ? » — dérivé DÉTERMINISTIQUEMENT de `last_seen_generation`
    vis-à-vis de la dernière génération observée pour la spécialité (voir
    `apex.metalearner.weakness_engine._improvement_status`), jamais une
    estimation qualitative."""

    #: Observée à la dernière génération connue de la spécialité.
    PERSISTENT = "persistent"
    #: Absente de la dernière génération, présente à l'avant-dernière.
    RECOVERING = "recovering"
    #: Absente des deux dernières générations, mais observée avant.
    RESOLVED = "resolved"


@dataclass(frozen=True)
class JudgeLearningSignal:
    """Le feedback structuré d'UN `JudgeResult`, dans le contexte d'UN
    `GauntletResult` — jamais un texte narratif seul (§10 N+8, repris ici).
    `genome_version_id` permet la corrélation par lignage (§5 de la
    mission)."""

    id: str
    gauntlet_result_id: str
    member_id: str
    specialty: str
    generation: int
    genome_version_id: str
    judge_id: str
    judge_version: str
    dimension: JudgeDimension
    decision: JudgeDecision
    category: SignalCategory
    failure_category: FailureCategory | None
    severity: JudgeSeverity | None
    score: float | None
    confidence: float | None
    finding_summary: str
    evidence_reference: str | None
    protocol_version: str
    created_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def new(
        cls,
        *,
        gauntlet_result_id: str,
        member_id: str,
        specialty: str,
        generation: int,
        genome_version_id: str,
        judge_id: str,
        judge_version: str,
        dimension: JudgeDimension,
        decision: JudgeDecision,
        category: SignalCategory,
        failure_category: FailureCategory | None,
        severity: JudgeSeverity | None,
        score: float | None,
        confidence: float | None,
        finding_summary: str,
        evidence_reference: str | None,
        protocol_version: str,
    ) -> JudgeLearningSignal:
        return cls(
            id=str(uuid.uuid4()), gauntlet_result_id=gauntlet_result_id, member_id=member_id,
            specialty=specialty, generation=generation, genome_version_id=genome_version_id,
            judge_id=judge_id, judge_version=judge_version, dimension=dimension, decision=decision,
            category=category, failure_category=failure_category, severity=severity, score=score,
            confidence=confidence, finding_summary=finding_summary, evidence_reference=evidence_reference,
            protocol_version=protocol_version,
        )

    @classmethod
    def from_row(cls, row: JudgeLearningSignalRow) -> JudgeLearningSignal:
        return cls(
            id=row.id, gauntlet_result_id=row.gauntlet_result_id, member_id=row.member_id,
            specialty=row.specialty, generation=row.generation, genome_version_id=row.genome_version_id,
            judge_id=row.judge_id, judge_version=row.judge_version, dimension=JudgeDimension(row.dimension),
            decision=JudgeDecision(row.decision), category=SignalCategory(row.category),
            failure_category=FailureCategory(row.failure_category) if row.failure_category else None,
            severity=JudgeSeverity(row.severity) if row.severity else None, score=row.score,
            confidence=row.confidence, finding_summary=row.finding_summary,
            evidence_reference=row.evidence_reference, protocol_version=row.protocol_version,
            created_at=row.created_at,
        )


#: Catégories qui comptent RÉELLEMENT comme une preuve de faiblesse du
#: CANDIDAT (§4/§11) — JUDGE_FAILURE/INFRASTRUCTURE_FAILURE/
#: INSUFFICIENT_EVIDENCE en sont explicitement exclues : agréger ces
#: dernières dans une « faiblesse » attribuerait au candidat une panne qui
#: n'est pas la sienne.
WEAKNESS_SIGNAL_CATEGORIES = frozenset({SignalCategory.WEAKNESS, SignalCategory.CRITICAL_FAILURE})


@dataclass(frozen=True)
class GauntletWeakness:
    """Agrégat DÉRIVÉ des `JudgeLearningSignal` réels — jamais persisté
    séparément (même discipline qu'`apex.weakness.model.WeaknessReport`,
    recalculé à la demande depuis la preuve, aucune seconde source de
    vérité). Voir `apex.metalearner.weakness_engine` pour la formule
    déterministe exacte de chaque champ."""

    specialty: str
    dimension: JudgeDimension
    failure_category: FailureCategory
    severity: JudgeSeverity
    frequency: int
    confidence: float
    first_seen: datetime
    last_seen: datetime
    first_seen_generation: int
    last_seen_generation: int
    affected_candidates: tuple[str, ...]
    affected_generations: tuple[int, ...]
    affected_genomes: tuple[str, ...]
    evidence_count: int
    recurrence_count: int
    improvement_status: ImprovementStatus
    #: `None` sauf si TOUS les candidats affectés partagent un ancêtre
    #: commun réel (`apex.metalearner.population_service.PopulationService.
    #: get_lineage`) — jamais une causalité affirmée, seulement une
    #: corrélation observée (§5 : « suspected_inherited », jamais
    #: « confirmed »).
    suspected_inherited_from: str | None = None


@dataclass(frozen=True)
class CurriculumTarget:
    """Recommandation PRODUITE par le Curriculum Engine (§6) — ne modifie
    JAMAIS un score directement (§6 : « il produit des expériences/tâches
    d'évaluation »). `recommended_mode`/`base_task_id` restent `None`
    quand aucun `GenerationMode` réel ne couvre la dimension observée
    (`BUG_FARM` reste `PLANNED` comme mode de génération — jamais fabriqué
    ici — voir la docstring d'`apex.metalearner.curriculum_engine`).

    `edge_case_kind`/`adversarial_kind`/`generalization_variant` (passe
    « Adversarial Curriculum + Capability Benchmarking », N+10, additifs) —
    paramètres SUPPLÉMENTAIRES requis par `apex.taskgen.deterministic.
    DeterministicTaskGenerator.generate()` quand `recommended_mode` est
    `EDGE_CASE`/`ADVERSARIAL`, ou quand `NORMAL` porte une variante de
    généralisation (§6 de la mission N+10) — `None` sinon."""

    specialty: str
    dimension: JudgeDimension
    weakness: GauntletWeakness
    recommended_mode: GenerationMode | None
    base_task_id: str | None
    focus_genome_paths: tuple[str, ...]
    rationale: str
    edge_case_kind: EdgeCaseKind | None = None
    adversarial_kind: AdversarialKind | None = None
    generalization_variant: GeneralizationVariant | None = None


@dataclass(frozen=True)
class EvolutionContext:
    """Contexte explicite (§8 de la mission) passé à UNE `evolve_generation`
    — jamais un second moteur d'évolution : `PopulationEvolutionService`
    reste l'UNIQUE point d'exécution, ce contexte ne fait qu'informer SES
    décisions de mutation (`focus_genome_paths`) avec des preuves réelles
    déjà agrégées."""

    specialty: str
    active_weaknesses: tuple[GauntletWeakness, ...]
    curriculum_targets: tuple[CurriculumTarget, ...]
    focus_genome_paths: tuple[str, ...]
