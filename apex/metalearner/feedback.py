"""`LearningFeedbackService` — connecte un cycle d'apprentissage autonome
déjà réel (`apex.metalearner.cycle.run_experiment_cycle`, via
`apex.metalearner.autonomy.AutonomousLearningService`) aux systèmes Skill
et Genome déjà réels, sans jamais les réimplémenter.

Passe « Skill + Genome Integration into the Autonomous Learning Loop ».

    ExperimentCycleResult (déjà réel)
        -> Skill : SkillRegistryService.propose_from_evidence (Phase 14,
           déjà réel — même seuil de preuve calibrée que le reste du
           registre, `MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE`)
        -> Genome : GenomeStoreService.propose (Phase 17, déjà réel) +
           apex.genome.mutation.mutate (Phase 17, déjà réel, PURE)

**Ni promotion ni décision ici** : `propose`/`propose_from_evidence`
autorisent `Subject.CORE` (voir `apex.policy.engine` — même motif que
`Action.CANCEL_MISSION` : une synthèse automatique n'est jamais une
décision) ; `PROMOTE_SKILL`/`PROMOTE_GENOME_VERSION` restent HUMAIN seul,
jamais appelés par ce module — §8 anti-auto-illusion, invariant déjà
verrouillé, jamais contourné ici.

**Idempotence** : chaque cycle porte un `experiment_ref` unique (déjà
introduit en passe « Autonomous Learning Trigger »). Avant tout traitement,
`process_cycle_result` cherche un événement `learning.feedback_processed`
déjà écrit pour CET `experiment_ref` (`EventRepository`, déjà réel — même
mécanisme que `AutonomousLearningService.history`, aucune nouvelle table) ;
s'il existe, le résultat précédent est simplement rejoué, jamais retraité —
aucun doublon de Skill/GenomeVersion, quel que soit le nombre d'appels.

**Isolation des pannes** : une exception dans le traitement Skill ou
Genome est journalisée (`learning.skill_feedback_failed`/
`learning.genome_feedback_failed`) et n'interrompt JAMAIS le traitement de
l'autre, ni ne remonte à l'appelant — `process_cycle_result` ne lève que
si l'écriture de l'événement `learning.feedback_processed` final
échoue elle-même (DB indisponible), auquel cas le cycle appelant (voir
`AutonomousLearningService._run_cycle`) l'attrape aussi et ne fait jamais
échouer un cycle dont l'expérience elle-même a réussi.

**Sécurité contre l'accumulation non contrôlée** (Skill ET Genome,
symétriquement) : si une compétence `PROPOSED` existe déjà pour la
spécialité, ou une `GenomeVersion` `PROPOSED` existe déjà (toutes
spécialités confondues — une seule évolution du Genome global à la fois),
aucun nouveau candidat n'est créé — un humain doit d'abord décider
(`promote`/`reject`) avant qu'un nouveau candidat puisse apparaître. Sans
cette garde, chaque cycle qualifiant ajouterait indéfiniment un nouveau
candidat jamais décidé.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from apex.metalearner.model import ExperimentCycleResult
from apex.policy.model import Subject

#: Même seuil que la proposition manuelle de compétence
#: (`apex.skills.service.propose_from_evidence`) — réutilisé tel quel pour
#: la proposition de mutation de Genome : aucun second seuil arbitraire
#: inventé pour une décision de même nature (« la preuve est-elle assez
#: favorable pour justifier une proposition, jamais une promotion »).
from apex.skills.service import MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE

#: Taux/magnitude de mutation par défaut — valeurs conservatrices,
#: arbitraires mais documentées (même discipline que
#: `apex.weakness.detector`/`apex.skills.service` pour leurs propres
#: seuils) : 10 % des feuilles numériques/booléennes mutées, de ±10 % au
#: maximum. Ajustable uniquement sur preuve, jamais pour « avoir l'air
#: plus agressif ».
DEFAULT_MUTATION_RATE = 0.1
DEFAULT_MUTATION_MAGNITUDE = 0.1

_EVENT_LOGGER = "apex.metalearner.feedback"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _deterministic_seed(experiment_ref: str) -> int:
    """Dérive un entier déterministe de `experiment_ref` — même
    `experiment_ref` retraité (rejeu idempotent, ou test) produit
    EXACTEMENT la même mutation, jamais un résultat qui varie d'un appel à
    l'autre pour la même preuve."""
    digest = hashlib.sha256(experiment_ref.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


@dataclass(frozen=True)
class LearningFeedbackOutcome:
    """Métadonnées ADDITIVES sur un `ExperimentCycleResult` déjà complet
    (`task`/`eval_run`/`weakness_before`/`weakness_after` y restent — jamais
    dupliqués ici, voir `apex.metalearner.model.ExperimentCycleResult`).
    `*_candidate_reason` porte TOUJOURS une explication, y compris quand
    aucun candidat n'a été créé (preuve insuffisante, doublon évité, panne
    isolée) — jamais un champ silencieusement vide sans justification."""

    experiment_ref: str
    specialty: str
    skill_candidate_id: str | None
    skill_candidate_reason: str
    genome_candidate_id: str | None
    genome_candidate_reason: str
    processed_at: datetime


class LearningFeedbackService:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def process_cycle_result(
        self, result: ExperimentCycleResult, *, experiment_ref: str,
    ) -> LearningFeedbackOutcome:
        """Point d'entrée unique, appelé par `AutonomousLearningService`
        après un cycle réussi. Idempotent sur `experiment_ref` : un second
        appel avec la même valeur ne crée jamais de second candidat."""
        existing = self._find_existing_outcome(experiment_ref)
        if existing is not None:
            return existing

        specialty = result.decision.specialty
        skill_id, skill_reason = self._process_skill(specialty, experiment_ref)
        genome_id, genome_reason = self._process_genome(specialty, experiment_ref)

        outcome = LearningFeedbackOutcome(
            experiment_ref=experiment_ref, specialty=specialty,
            skill_candidate_id=skill_id, skill_candidate_reason=skill_reason,
            genome_candidate_id=genome_id, genome_candidate_reason=genome_reason,
            processed_at=_utcnow(),
        )
        self._record_event(
            "learning.feedback_processed",
            {
                "experiment_ref": experiment_ref, "specialty": specialty,
                "skill_candidate_id": skill_id, "skill_candidate_reason": skill_reason,
                "genome_candidate_id": genome_id, "genome_candidate_reason": genome_reason,
            },
        )
        return outcome

    # -- Skill ----------------------------------------------------------------

    def _process_skill(self, specialty: str, experiment_ref: str) -> tuple[str | None, str]:
        """Jamais un échec Skill ne remonte : preuve insuffisante
        (`ValueError`, déjà le comportement documenté de
        `propose_from_evidence`) et panne inattendue sont toutes deux
        capturées ici, la seconde en plus journalisée pour observabilité
        (§13) — le traitement Genome qui suit n'est jamais empêché."""
        try:
            from apex.skills.model import SkillStatus
            from apex.skills.service import SkillRegistryService

            skills = SkillRegistryService(self._session_factory)
            pending = [s for s in skills.list_by_specialty(specialty) if s.status is SkillStatus.PROPOSED]
            if pending:
                return None, (
                    f"aucun candidat proposé — une compétence est déjà PROPOSED pour "
                    f"« {specialty} » ({pending[0].id}), évite l'accumulation non contrôlée"
                )

            skill = skills.propose_from_evidence(
                name=f"Compétence observée — {specialty}",
                specialty=specialty,
                description=(
                    f"Proposition automatique (Subject.CORE) depuis le cycle d'apprentissage "
                    f"autonome {experiment_ref} — preuve calibrée réelle sur la spécialité "
                    f"« {specialty} » (voir WeaknessDetectorService/ConfidenceCalibrator)."
                ),
                created_by=Subject.CORE,
            )
        except ValueError as exc:
            return None, f"aucun candidat proposé — preuve insuffisante : {exc}"
        except Exception as exc:  # panne isolée : ne bloque jamais le traitement Genome
            self._record_event(
                "learning.skill_feedback_failed",
                {"experiment_ref": experiment_ref, "specialty": specialty, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return None, f"échec inattendu, isolé : {exc.__class__.__name__}: {exc}"
        else:
            self._record_event(
                "learning.skill_feedback_proposed",
                {"experiment_ref": experiment_ref, "specialty": specialty, "skill_id": skill.id},
            )
            return skill.id, "candidat proposé depuis une preuve calibrée réelle (non promu)"

    # -- Genome -----------------------------------------------------------------

    def _process_genome(self, specialty: str, experiment_ref: str) -> tuple[str | None, str]:
        """Frontière d'intégration volontairement MINIMALE et conservatrice
        (§5 de la mission) : le contenu du Genome n'est aujourd'hui raccordé
        à AUCUN comportement réel de clone (voir la docstring de
        `apex.genome.service.GenomeStoreService`) — une mutation
        automatique reste donc une PROPOSITION de donnée versionnée,
        jamais une décision. Refuse de proposer si une version est déjà
        `PROPOSED` (jamais plus d'un candidat non décidé à la fois — évite
        toute accumulation incontrôlée, `§ population safety`)."""
        try:
            from apex.calibration.calibrator import ConfidenceCalibrator
            from apex.calibration.model import CalibrationStatus
            from apex.genome.model import GenomeVersionOrigin, GenomeVersionStatus
            from apex.genome.mutation import mutate
            from apex.genome.service import GenomeStoreService
            from apex.weakness.service import WeaknessDetectorService

            report = WeaknessDetectorService(self._session_factory).detect_for_specialty(specialty)
            calibrated = ConfidenceCalibrator().calibrate_from_weakness_report(report)
            if calibrated.status is CalibrationStatus.INSUFFICIENT_DATA:
                return None, f"aucun candidat proposé — preuve insuffisante : {calibrated.reason}"
            bound = calibrated.calibrated_lower_bound
            if bound is None:
                # Ne devrait jamais arriver hors INSUFFICIENT_DATA (voir
                # CalibratedConfidence) — garde défensive pour le narrowing
                # de type, jamais atteinte en pratique.
                return None, "aucun candidat proposé — borne inférieure calibrée indisponible"
            if bound < MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE:
                return None, (
                    f"aucun candidat proposé — borne inférieure calibrée {bound:.0%} sous le "
                    f"seuil requis {MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE:.0%}"
                )

            genome = GenomeStoreService(self._session_factory)
            current = genome.get_current()
            if current is None:
                return None, "aucun candidat proposé — aucune GenomeVersion PROMOTED à faire évoluer"

            pending = [v for v in genome.list_all(limit=200) if v.status is GenomeVersionStatus.PROPOSED]
            if pending:
                return None, (
                    f"aucun candidat proposé — une version est déjà PROPOSED "
                    f"({pending[0].id}), évite l'accumulation non contrôlée"
                )

            content = genome.get_content(current.id)
            mutated_content = mutate(
                content, rate=DEFAULT_MUTATION_RATE, magnitude=DEFAULT_MUTATION_MAGNITUDE,
                seed=_deterministic_seed(experiment_ref),
            )
            version = genome.propose(
                content=mutated_content, created_by=Subject.CORE,
                origin=GenomeVersionOrigin.MUTATION, parent_version_id=current.id,
            )
        except Exception as exc:  # panne isolée : le candidat Skill déjà traité n'est jamais perdu
            self._record_event(
                "learning.genome_feedback_failed",
                {"experiment_ref": experiment_ref, "specialty": specialty, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return None, f"échec inattendu, isolé : {exc.__class__.__name__}: {exc}"
        else:
            self._record_event(
                "learning.genome_feedback_proposed",
                {
                    "experiment_ref": experiment_ref, "specialty": specialty,
                    "genome_version_id": version.id, "parent_version_id": current.id,
                },
            )
            return version.id, "mutation proposée depuis une preuve calibrée réelle (non promue)"

    # -- idempotence ------------------------------------------------------------

    def _find_existing_outcome(self, experiment_ref: str) -> LearningFeedbackOutcome | None:
        """Même patron que `AutonomousLearningService.history` : parcourt
        les événements récents plutôt que d'ajouter un index dédié — pas un
        chemin chaud, appelé une fois par cycle."""
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            for row in EventRepository(session).recent(limit=500):
                if row.logger != _EVENT_LOGGER or row.msg != "learning.feedback_processed":
                    continue
                ctx = row.context or {}
                if ctx.get("experiment_ref") != experiment_ref:
                    continue
                return LearningFeedbackOutcome(
                    experiment_ref=experiment_ref, specialty=ctx.get("specialty", ""),
                    skill_candidate_id=ctx.get("skill_candidate_id"),
                    skill_candidate_reason=ctx.get("skill_candidate_reason", ""),
                    genome_candidate_id=ctx.get("genome_candidate_id"),
                    genome_candidate_reason=ctx.get("genome_candidate_reason", ""),
                    processed_at=row.ts,
                )
        return None

    def _record_event(self, msg: str, context: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(ts=_utcnow(), level="INFO", logger=_EVENT_LOGGER, msg=msg, context=context)
