"""Vocabulaire de la vue Self-Model — READ-ONLY, jamais une source de
vérité indépendante (aucune table miroir, aucune donnée dupliquée qui
pourrait diverger de sa source). Chaque champ reste traçable jusqu'au
composant qui le mesure réellement — voir `apex.selfmodel.view`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apex.calibration.model import CalibratedConfidence
from apex.costgain.model import ResourceCost
from apex.evalrunner.model import EvalMethod
from apex.oracles.model import OracleVerdict
from apex.skills.model import Skill
from apex.weakness.model import WeaknessReport


@dataclass(frozen=True)
class ProvenanceEntry:
    """Un maillon RÉEL de la chaîne « pourquoi APEX pense-t-il savoir
    cela ? » — un `EvalRun` avec son contexte complet, jamais un résumé qui
    perdrait la traçabilité vers l'`EvalRun.id` d'origine."""

    eval_run_id: str
    task_id: str
    task_specialty: str
    clone_id: str
    #: `None` seulement si le clone d'origine n'existe plus/est illisible —
    #: jamais une valeur devinée.
    clone_genome_ref: str | None
    method: EvalMethod
    oracle_verdict: OracleVerdict
    oracle_detail: str | None
    dataset_ref: str | None
    created_at: datetime


@dataclass(frozen=True)
class SkillProvenance:
    """Skill -> evidence_run_ids -> EvalRun -> Task/clone/genome_ref/
    dataset/oracle -> résultat — la chaîne complète, dérivée à la lecture,
    jamais stockée séparément (voir `apex.selfmodel.view.
    trace_skill_provenance`)."""

    skill: Skill
    entries: tuple[ProvenanceEntry, ...]


@dataclass(frozen=True)
class SpecialtyKnowledge:
    """Ce qu'APEX sait (et ne sait pas) d'UNE spécialité, à l'instant de la
    requête — dérivé de `SkillRegistry`/`WeaknessDetector`/
    `ConfidenceCalibrator`/`EvalRunner`/`CostGainEstimator`, jamais recalculé
    ou stocké ici : une requête, pas une table."""

    specialty: str
    acquired_skills: tuple[Skill, ...]
    proposed_skills: tuple[Skill, ...]
    weakness_report: WeaknessReport
    calibrated_confidence: CalibratedConfidence
    eval_run_count: int
    #: Distinct des `EvalMethod` (`practice`, `research`, ...) réellement
    #: observés — PAS le `GenerationMode` choisi par `MetaLearner`, qui
    #: n'est pas encore associé à un `EvalRun` (limite réelle, voir
    #: `apex.metalearner`, priorité future).
    methods_used: tuple[EvalMethod, ...]
    genome_refs_used: tuple[str, ...]
    cost: ResourceCost
    last_skill_validated_at: datetime | None


@dataclass(frozen=True)
class SelfModelView:
    """« Que sait actuellement APEX ? » — une agrégation READ-ONLY sur les
    spécialités demandées, jamais une source de vérité : chaque appel relit
    les composants réels, aucun état conservé entre deux appels (même
    discipline que `MetaLearner` — aucune illusion de mémoire propre)."""

    specialties: tuple[SpecialtyKnowledge, ...]
    generated_at: datetime
