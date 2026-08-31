"""SkillRegistryService — proposition, promotion, rejet, révision d'une
compétence.

Registre : `SkillRegistry`, phase 14 — « compétences versionnées, graphe de
prérequis, promotion » (`docs/VISION_INTEGRATION.md` §2.2). Chaque méthode à
effet de bord passe par `PolicyEngine` en premier — même discipline que
`apex.missions.service`/`apex.tasks.service`.

Cycle de promotion **HUMAIN** ici, jamais celui de `PromotionGovernor`
(`PLANNED`, phase 16, qui gouverne la promotion vers le Core — un sujet
entièrement différent). `Action.PROPOSE_SKILL` est accordée à CORE (synthèse
automatique depuis `EvalRunner`) ET HUMAIN (saisie manuelle) — même motif que
`Action.CANCEL_MISSION` ; `PROMOTE_SKILL`/`REJECT_SKILL` restent HUMAIN
seul : §8 anti-auto-illusion, aucune compétence ne peut s'auto-promouvoir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.calibration.calibrator import ConfidenceCalibrator
from apex.calibration.model import CalibrationStatus
from apex.missions.dependencies import DependencyCycle, detect_cycle
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.skills.model import Skill, SkillStatus
from apex.skills.repository import SkillRepository
from apex.specialties.registry import by_id as specialty_by_id
from apex.weakness.service import WeaknessDetectorService

#: Sous cette borne INFÉRIEURE calibrée (intervalle de Wilson à 95 %,
#: `apex.calibration.calibrator`), `propose_from_evidence` refuse de
#: proposer une compétence — délibérément la borne basse, jamais le point
#: estimate ni une confiance déclarée : une petite série chanceuse ne doit
#: pas suffire. Valeur arbitraire mais documentée, ajustable seulement sur
#: preuve (même discipline que `apex.weakness.detector`).
MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE = 0.7


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillRegistryService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._repo = SkillRepository(session_factory)

    # -- proposition -----------------------------------------------------

    def propose(
        self,
        *,
        name: str,
        specialty: str,
        description: str,
        created_by: Subject,
        prerequisite_skill_ids: tuple[str, ...] = (),
        evidence_run_ids: tuple[str, ...] = (),
        confidence: float | None = None,
    ) -> Skill:
        self.policy.authorize(created_by, Action.PROPOSE_SKILL)
        self._validate_common_fields(
            name=name, description=description, specialty=specialty, confidence=confidence,
        )
        self._validate_prerequisites(prerequisite_skill_ids)
        self._validate_evidence(evidence_run_ids)

        skill = Skill.new(
            name=name, specialty=specialty, description=description, created_by=created_by.value,
            prerequisite_skill_ids=prerequisite_skill_ids, evidence_run_ids=evidence_run_ids,
            confidence=confidence,
        )
        self._repo.create(skill)
        self._record_event(skill, "skill.proposed", {})
        self._record_audit(
            skill, actor=created_by.value, action="skill.proposed",
            details={"specialty": specialty, "prerequisite_skill_ids": list(prerequisite_skill_ids)},
        )
        return skill

    def propose_from_evidence(
        self,
        *,
        name: str,
        specialty: str,
        description: str,
        created_by: Subject,
        prerequisite_skill_ids: tuple[str, ...] = (),
        min_calibrated_lower_bound: float = MIN_CALIBRATED_LOWER_BOUND_FOR_SKILL_EVIDENCE,
        weakness_service: WeaknessDetectorService | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> Skill:
        """Dérive `evidence_run_ids`/`confidence` de résultats RÉELS et
        INDÉPENDANTS plutôt que de les recevoir de l'appelant — `propose()`
        reste disponible pour une saisie manuelle déjà justifiée par un
        appelant qui a lui-même rassemblé sa preuve. Compose
        `WeaknessDetectorService` (Phase 12, agrège les `EvalRun` réels de la
        spécialité — jugés par `OracleProvider`/`JudgeTier`, jamais par le
        clone évalué) et `ConfidenceCalibrator` (Phase 12, intervalle de
        Wilson) — aucune logique statistique dupliquée ici.

        Jamais une compétence proposée sur la seule affirmation d'un clone
        (§8 anti-auto-illusion) : le déclencheur est la BORNE INFÉRIEURE
        calibrée (conservatrice par construction), jamais le point estimate
        ni une confiance déclarée — une petite série chanceuse ne suffit
        pas. `evidence_run_ids` porte TOUS les runs considérés (succès et
        échecs, voir `WeaknessDetector.detect`) : un échec ne disparaît
        jamais de la preuve, même quand la compétence est proposée malgré
        lui.
        """
        weakness_service = weakness_service or WeaknessDetectorService(self._session_factory)
        calibrator = calibrator or ConfidenceCalibrator()

        report = weakness_service.detect_for_specialty(specialty)
        calibrated = calibrator.calibrate_from_weakness_report(report)

        if calibrated.status is CalibrationStatus.INSUFFICIENT_DATA:
            raise ValueError(
                f"preuve insuffisante pour proposer une compétence en « {specialty} » : "
                f"{calibrated.reason}"
            )
        if calibrated.calibrated_lower_bound < min_calibrated_lower_bound:
            raise ValueError(
                f"preuve insuffisamment favorable pour proposer une compétence en "
                f"« {specialty} » (borne inférieure calibrée "
                f"{calibrated.calibrated_lower_bound:.0%} < seuil requis "
                f"{min_calibrated_lower_bound:.0%}, sur {calibrated.sample_size} "
                f"observation(s) réelle(s), {report.fail_count} échec(s) parmi elles)"
            )

        skill = self.propose(
            name=name, specialty=specialty, description=description, created_by=created_by,
            prerequisite_skill_ids=prerequisite_skill_ids, evidence_run_ids=report.evidence_run_ids,
            confidence=calibrated.calibrated_point_estimate,
        )
        self._record_audit(
            skill, actor=created_by.value, action="skill.proposed_from_evidence",
            details={
                "weakness_category": report.category.value,
                "sample_size": calibrated.sample_size,
                "calibrated_lower_bound": calibrated.calibrated_lower_bound,
                "calibrated_upper_bound": calibrated.calibrated_upper_bound,
                "fail_count": report.fail_count,
                "calibration_reason": calibrated.reason,
            },
        )
        return skill

    def revise(
        self,
        skill_id: str,
        *,
        name: str,
        description: str,
        created_by: Subject,
        prerequisite_skill_ids: tuple[str, ...] = (),
        evidence_run_ids: tuple[str, ...] = (),
        confidence: float | None = None,
    ) -> Skill:
        """Insère une NOUVELLE compétence (`status=PROPOSED`, sa propre
        décision à venir) qui remplacera `skill_id` — jamais une mutation du
        contenu existant. Refuse si `skill_id` n'est pas `ACQUIRED`, ou a
        déjà une révision — même garde que `MemoryService.revise`
        (`find_successor`, chaîne linéaire)."""
        self.policy.authorize(created_by, Action.PROPOSE_SKILL)
        original = self._require(skill_id)
        if original.status is not SkillStatus.ACQUIRED:
            raise ValueError(
                f"seule une compétence ACQUIRED peut être révisée (« {skill_id} » est "
                f"{original.status.value})"
            )
        if self._repo.find_successor(skill_id) is not None:
            raise ValueError(f"la compétence « {skill_id} » a déjà une révision — chaîne linéaire seule")

        self._validate_common_fields(
            name=name, description=description, specialty=original.specialty, confidence=confidence,
        )
        self._validate_prerequisites(prerequisite_skill_ids)
        self._validate_evidence(evidence_run_ids)

        revised = Skill.new(
            name=name, specialty=original.specialty, description=description, created_by=created_by.value,
            prerequisite_skill_ids=prerequisite_skill_ids, evidence_run_ids=evidence_run_ids,
            confidence=confidence, supersedes_id=skill_id,
        )
        self._repo.create(revised)
        self._record_event(revised, "skill.revised", {"supersedes_id": skill_id})
        self._record_audit(
            revised, actor=created_by.value, action="skill.revised", details={"supersedes_id": skill_id},
        )
        return revised

    # -- décisions humaines ------------------------------------------------

    def promote(self, skill_id: str, *, requested_by: Subject, reason: str | None = None) -> Skill:
        self.policy.authorize(requested_by, Action.PROMOTE_SKILL)
        skill = self._require(skill_id)
        if skill.status is not SkillStatus.PROPOSED:
            raise ValueError(
                f"compétence « {skill_id} » déjà décidée ({skill.status.value}) — double décision refusée"
            )

        unmet = self._unmet_prerequisites(skill.prerequisite_skill_ids)
        if unmet:
            raise ValueError(
                f"prérequis non acquis pour « {skill_id} » : {sorted(unmet)} — promotion refusée"
            )

        decided = self._repo.decide(
            skill_id, status=SkillStatus.ACQUIRED, decided_by=requested_by.value,
            decided_at=_utcnow(), decision_reason=reason,
        )
        if decided is None:
            raise ValueError(f"compétence « {skill_id} » déjà décidée entre-temps — course perdue")
        self._record_event(decided, "skill.promoted", {"requested_by": requested_by.value})
        self._record_audit(decided, actor=requested_by.value, action="skill.promoted", details={"reason": reason})
        return decided

    def reject(self, skill_id: str, *, requested_by: Subject, reason: str | None = None) -> Skill:
        self.policy.authorize(requested_by, Action.REJECT_SKILL)
        skill = self._require(skill_id)
        if skill.status is not SkillStatus.PROPOSED:
            raise ValueError(
                f"compétence « {skill_id} » déjà décidée ({skill.status.value}) — double décision refusée"
            )

        decided = self._repo.decide(
            skill_id, status=SkillStatus.REJECTED, decided_by=requested_by.value,
            decided_at=_utcnow(), decision_reason=reason,
        )
        if decided is None:
            raise ValueError(f"compétence « {skill_id} » déjà décidée entre-temps — course perdue")
        self._record_event(decided, "skill.rejected", {"requested_by": requested_by.value})
        self._record_audit(decided, actor=requested_by.value, action="skill.rejected", details={"reason": reason})
        return decided

    # -- lecture -------------------------------------------------------------

    def get(self, skill_id: str) -> Skill | None:
        return self._repo.get(skill_id)

    def list_all(self, limit: int = 200) -> list[Skill]:
        return self._repo.list_all(limit=limit)

    def list_by_specialty(self, specialty: str, limit: int = 200) -> list[Skill]:
        return self._repo.list_by_specialty(specialty, limit=limit)

    def list_acquired(self, specialty: str | None = None, limit: int = 200) -> list[Skill]:
        skills = self._repo.list_by_specialty(specialty, limit=limit) if specialty else self._repo.list_all(limit=limit)
        return [s for s in skills if s.status is SkillStatus.ACQUIRED]

    # -- internes -----------------------------------------------------------

    def _validate_common_fields(
        self, *, name: str, description: str, specialty: str, confidence: float | None,
    ) -> None:
        if not name.strip():
            raise ValueError("le nom de la compétence est obligatoire")
        if not description.strip():
            raise ValueError("la description de la compétence est obligatoire")
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence doit être dans [0.0, 1.0]")

        spec = specialty_by_id(specialty)
        if spec is None:
            raise ValueError(f"spécialité « {specialty} » inconnue")
        if not spec.is_supported:
            raise ValueError(
                f"spécialité « {specialty} » est PLANNED — aucune compétence ne peut lui être "
                "attribuée pour l'instant"
            )

    def _validate_prerequisites(self, prerequisite_skill_ids: tuple[str, ...]) -> None:
        if not prerequisite_skill_ids:
            return
        graph = self._repo.dependency_graph()
        missing = [p for p in prerequisite_skill_ids if p not in graph]
        if missing:
            raise ValueError(f"prérequis introuvable(s) : {missing}")
        cycle = detect_cycle(graph, new_deps=prerequisite_skill_ids)
        if cycle is not None:
            raise DependencyCycle(f"dépendance cyclique détectée : {' -> '.join(cycle)}")

    def _validate_evidence(self, evidence_run_ids: tuple[str, ...]) -> None:
        if not evidence_run_ids:
            return
        from apex.evalrunner.repository import EvalRunRepository

        eval_repo = EvalRunRepository(self._session_factory)
        missing = [e for e in evidence_run_ids if eval_repo.get(e) is None]
        if missing:
            raise ValueError(f"preuve(s) introuvable(s) — EvalRun inexistant : {missing}")

    def _unmet_prerequisites(self, prerequisite_skill_ids: tuple[str, ...]) -> set[str]:
        unmet: set[str] = set()
        for prereq_id in prerequisite_skill_ids:
            prereq = self._repo.get(prereq_id)
            if prereq is None or prereq.status is not SkillStatus.ACQUIRED:
                unmet.add(prereq_id)
        return unmet

    def _require(self, skill_id: str) -> Skill:
        skill = self._repo.get(skill_id)
        if skill is None:
            raise LookupError(f"compétence « {skill_id} » introuvable")
        return skill

    def _record_event(self, skill: Skill, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.skills",
                msg=kind, context={"skill_id": skill.id, "kind": kind, **details},
            )

    def _record_audit(self, skill: Skill, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=skill.id, details=details,
            )
