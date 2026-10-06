"""MissionService — orchestrateur du cycle de vie d'une Mission.

Applique la chaîne obligatoire (docs/COGNITIVE_ARCHITECTURE.md, étape 6) :

    HUMAN -> API -> POLICY -> MISSION -> AGENT RUNTIME -> SANDBOX -> RESULT
    -> REVIEW -> HUMAN -> PROMOTION éventuelle

Chaque méthode qui a un effet de bord passe par `PolicyEngine` en premier —
AUCUNE règle de permission n'est codée ailleurs (ni dans les routes API, ni
dans le frontend). Un type de clone (`Subject.RESEARCH`, `.DEVELOPER`, ...)
n'a AUCUNE de ces actions dans son allow-list (`apex.policy.engine`) : il ne
peut donc jamais créer, valider, rejeter ou lancer sa propre mission — refus
par défaut, pas une liste noire séparée à maintenir.

**Mise à jour étape 7** : `mark_running`/`mark_review`/`mark_failed` sont
désormais appelées par `apex.missions.execution.launch_mission`, qui relie
réellement Mission Engine à `AgentRuntime`/`SandboxProvider` — mais AUCUNE
route API n'expose encore ce lancement (voir `apex/api/routes/missions.py`) :
une mission créée via l'API reste QUEUED tant que rien n'appelle
`launch_mission` directement (aujourd'hui : tests, ou un futur appel CORE).
La seule charge de travail réellement exécutable est le diagnostic RESEARCH
(`apex.missions.workloads`) — `TaskBank`/`EvalRunner`, PLANNED, n'existent
toujours pas pour dériver un « travail » d'une mission arbitraire.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.missions.dependencies import DependencyCycle, detect_cycle
from apex.missions.model import Mission, MissionPriority, ValidationStatus
from apex.missions.repository import MissionRepository
from apex.missions.states import InvalidMissionTransition, MissionState
from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.specialties.registry import by_id as specialty_by_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MissionService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._repo = MissionRepository(session_factory)

    # -- création --------------------------------------------------------

    def create(
        self,
        *,
        title: str,
        description: str,
        specialty: str,
        objective: str,
        created_by: Subject,
        priority: MissionPriority = MissionPriority.NORMAL,
        constraints: str | None = None,
        assigned_agent: str | None = None,
        agent_id: str | None = None,
        depends_on: tuple[str, ...] = (),
        task_id: str | None = None,
        task_options: dict[str, Any] | None = None,
    ) -> Mission:
        self.policy.authorize(created_by, Action.CREATE_MISSION)

        if not title.strip():
            raise ValueError("le titre de la mission est obligatoire")
        if not objective.strip():
            raise ValueError("l'objectif de la mission est obligatoire")

        spec = specialty_by_id(specialty)
        if spec is None:
            raise ValueError(f"spécialité « {specialty} » inconnue")
        if not spec.is_supported:
            raise ValueError(
                f"spécialité « {specialty} » est PLANNED — aucune mission ne peut lui être "
                "assignée pour l'instant"
            )

        if assigned_agent is not None:
            row = self._get_agent_row(assigned_agent)
            if row is None:
                raise ValueError(f"agent « {assigned_agent} » introuvable")
            if Subject(row.type) not in spec.compatible_agent_types:
                raise ValueError(
                    f"agent de type « {row.type} » incompatible avec la spécialité « {specialty} » "
                    f"(types compatibles : {sorted(s.value for s in spec.compatible_agent_types) or 'aucun'})"
                )

        if agent_id is not None:
            configured = self._get_configured_agent_row(agent_id)
            if configured is None:
                raise ValueError(f"agent « {agent_id} » introuvable")
            if not configured.enabled:
                raise ValueError(f"agent « {agent_id} » désactivé — ne peut être assigné à une mission")

        normalized_options: dict[str, Any] | None = None
        if task_id is not None:
            # Unification Missions/TaskLoop : une mission à Task vérifiable suit UN SEUL chemin, le TaskLoop
            # (qui crée lui-même ses clones) — jamais un mélange implicite avec un clone ou un Agent assigné.
            if assigned_agent is not None or agent_id is not None:
                raise ValueError("une mission à tâche vérifiable (task_id) n'accepte ni assigned_agent ni agent_id")
            from apex.missions.task_options import normalize_task_options
            from apex.tasks.repository import TaskRepository

            task = TaskRepository(self._session_factory).get(task_id)
            if task is None:
                raise ValueError(f"tâche « {task_id} » introuvable")
            if task.solution_harness is None:
                raise ValueError(f"la tâche « {task_id} » n'a pas de juge (« solution_harness ») : rien à vérifier")
            normalized_options = normalize_task_options(task_options, task=task)
        elif task_options:
            raise ValueError("task_options exige task_id")

        depends_on = tuple(depends_on)
        if depends_on:
            # Vérification TRANSACTIONNELLE (étape 8) : un seul aller-retour
            # DB fournit à la fois l'existence des dépendances ET le graphe
            # nécessaire à la détection de cycle — pas deux lectures
            # séparées qui pourraient diverger.
            graph = self._repo.dependency_graph()
            missing = [d for d in depends_on if d not in graph]
            if missing:
                raise ValueError(f"dépendance(s) introuvable(s) : {missing}")
            cycle = detect_cycle(graph, new_deps=depends_on)
            if cycle is not None:
                raise DependencyCycle(f"dépendance cyclique détectée : {' -> '.join(cycle)}")

        mission = Mission.new(
            title=title, description=description, specialty=specialty, objective=objective,
            created_by=created_by.value, priority=priority, constraints=constraints,
            assigned_agent=assigned_agent, agent_id=agent_id, depends_on=depends_on,
            task_id=task_id, task_options=normalized_options,
        )
        self._repo.create(mission)
        self._record_event(mission, "mission.created", {})

        mission.transition(MissionState.QUEUED)
        self._repo.update(mission, state=mission.state.value)
        self._record_event(mission, "mission.queued", {})
        self._record_audit(
            mission, actor=created_by.value, action="mission.created",
            details={"specialty": specialty, "assigned_agent": assigned_agent, "task_id": task_id},
        )
        return mission

    def attach_task_run(self, mission_id: str, task_run_id: str) -> Mission:
        """Relie la mission (RUNNING) à la TaskRun que le TaskLoop exécute pour elle — trace, jamais décision."""
        mission = self._require(mission_id)
        mission.task_run_id = task_run_id
        self._repo.update(mission, state=mission.state.value, task_run_id=task_run_id)
        self._record_event(mission, "mission.task_run_started", {"task_run_id": task_run_id})
        return mission

    # -- décisions humaines ------------------------------------------------

    def cancel(self, mission_id: str, *, requested_by: Subject) -> Mission:
        self.policy.authorize(requested_by, Action.CANCEL_MISSION)
        mission = self._require(mission_id)
        mission.transition(MissionState.CANCELLED)  # lève si déjà terminale
        mission.completed_at = _utcnow()
        self._repo.update(mission, state=mission.state.value, completed_at=mission.completed_at)
        self._record_event(mission, "mission.cancelled", {"requested_by": requested_by.value})
        self._record_audit(
            mission, actor=requested_by.value, action="mission.cancelled", details={},
        )
        return mission

    def validate(self, mission_id: str, *, requested_by: Subject) -> Mission:
        self.policy.authorize(requested_by, Action.VALIDATE_MISSION)
        mission = self._require(mission_id)
        self._require_no_prior_decision(mission)
        mission.transition(MissionState.COMPLETED)  # lève si pas en REVIEW
        mission.validation_status = ValidationStatus.VALIDATED
        mission.completed_at = _utcnow()
        self._repo.update(
            mission, state=mission.state.value, validation_status=mission.validation_status.value,
            completed_at=mission.completed_at,
        )
        self._record_event(mission, "mission.validated", {"requested_by": requested_by.value})
        self._record_audit(mission, actor=requested_by.value, action="mission.validated", details={})
        return mission

    def reject(self, mission_id: str, *, requested_by: Subject, reason: str | None = None) -> Mission:
        self.policy.authorize(requested_by, Action.REJECT_MISSION)
        mission = self._require(mission_id)
        self._require_no_prior_decision(mission)
        mission.transition(MissionState.FAILED)  # lève si pas en REVIEW
        mission.validation_status = ValidationStatus.REJECTED
        mission.error = reason
        mission.completed_at = _utcnow()
        self._repo.update(
            mission, state=mission.state.value, validation_status=mission.validation_status.value,
            error=mission.error, completed_at=mission.completed_at,
        )
        self._record_event(mission, "mission.rejected", {"reason": reason})
        self._record_audit(mission, actor=requested_by.value, action="mission.rejected", details={"reason": reason})
        return mission

    def request_revision(self, mission_id: str, *, requested_by: Subject) -> Mission:
        """Renvoie la mission en DRAFT pour édition — non exposé par l'API
        dans cette étape (seuls validate/reject figurent dans les routes
        demandées), utilisable directement au niveau service."""
        self.policy.authorize(requested_by, Action.REQUEST_MISSION_REVISION)
        mission = self._require(mission_id)
        self._require_no_prior_decision(mission)
        mission.transition(MissionState.DRAFT)  # lève si pas en REVIEW
        mission.validation_status = ValidationStatus.REVISION_REQUESTED
        self._repo.update(mission, state=mission.state.value, validation_status=mission.validation_status.value)
        self._record_event(mission, "mission.revision_requested", {})
        self._record_audit(mission, actor=requested_by.value, action="mission.revision_requested", details={})
        return mission

    # -- transitions système (AGENT RUNTIME -> SANDBOX -> RESULT) -----------
    # Non exposées par l'API dans cette étape — voir la docstring du module.

    def mark_running(self, mission_id: str, *, requested_by: Subject = Subject.CORE) -> Mission:
        """QUEUED -> RUNNING via une réclamation ATOMIQUE en base (étape 8),
        pas un lire-puis-écrire : `MissionRepository.claim_running` exécute
        un seul UPDATE conditionnel (`WHERE state = 'queued'`). C'est ce qui
        garantit qu'un `SchedulerService` — ou plusieurs, concurremment —
        ne peut jamais faire progresser deux fois la même mission ; voir
        `apex.scheduler.service` et `tests/test_scheduler_service.py`.

        `InvalidMissionTransition` couvre à la fois « déjà RUNNING/terminale »
        (le cas historique) et « réclamée entre-temps par un autre appelant »
        (le nouveau cas étape 8) — même vocabulaire d'erreur, même garantie :
        aucun raccourci silencieux, jamais une double transition acceptée.
        """
        self.policy.authorize(requested_by, Action.LAUNCH_MISSION)
        mission = self._require(mission_id)
        started_at = _utcnow()
        claimed = self._repo.claim_running(mission_id, started_at=started_at)
        if not claimed:
            current = self._require(mission_id)
            raise InvalidMissionTransition(
                f"mission « {mission_id} » n'est plus QUEUED (état actuel : {current.state.value}) "
                "— déjà réclamée/lancée par ailleurs"
            )
        mission.transition(MissionState.RUNNING)
        mission.started_at = started_at
        self._record_event(mission, "mission.started", {})
        return mission

    def mark_review(self, mission_id: str, *, result: str) -> Mission:
        mission = self._require(mission_id)
        mission.transition(MissionState.REVIEW)  # lève si pas RUNNING
        mission.result = result
        self._repo.update(mission, state=mission.state.value, result=result)
        self._record_event(mission, "mission.review", {})
        return mission

    def mark_failed(self, mission_id: str, *, error: str) -> Mission:
        mission = self._require(mission_id)
        mission.transition(MissionState.FAILED)  # lève si pas RUNNING
        mission.error = error
        mission.completed_at = _utcnow()
        self._repo.update(
            mission, state=mission.state.value, error=error, completed_at=mission.completed_at,
        )
        self._record_event(mission, "mission.failed", {"error": error})
        return mission

    # -- lecture -------------------------------------------------------------

    def get(self, mission_id: str) -> Mission | None:
        return self._repo.get(mission_id)

    def list_all(self, limit: int = 100) -> list[Mission]:
        return self._repo.list_all(limit=limit)

    def list_queued(self, limit: int = 500) -> list[Mission]:
        """Étape 8 (Scheduler) — missions QUEUED, non triées par priorité
        ici (`apex.scheduler.service` applique le tri priorité/FIFO, qui
        connaît `MissionPriority`, un concept de domaine que cette couche
        volontairement pas de raison de dupliquer)."""
        return self._repo.list_by_state(MissionState.QUEUED.value, limit=limit)

    def list_running(self, limit: int = 500) -> list[Mission]:
        """Étape 8 — utilisé par `apex.scheduler.recovery.RecoveryService`
        pour trouver les missions RUNNING orphelines au démarrage."""
        return self._repo.list_by_state(MissionState.RUNNING.value, limit=limit)

    def count_running(self) -> int:
        """Étape 8 — compte RÉEL en base des missions RUNNING, utilisé par
        le Scheduler pour sa borne `max_concurrent_missions` : jamais un
        compteur en mémoire qui pourrait diverger entre plusieurs instances."""
        return self._repo.count_by_state(MissionState.RUNNING.value)

    # -- internes -----------------------------------------------------------

    def _require(self, mission_id: str) -> Mission:
        mission = self._repo.get(mission_id)
        if mission is None:
            raise LookupError(f"mission « {mission_id} » introuvable")
        return mission

    def _require_no_prior_decision(self, mission: Mission) -> None:
        if mission.validation_status is not None:
            raise ValueError(
                f"mission « {mission.id} » a déjà reçu une décision humaine "
                f"({mission.validation_status.value}) — double validation/rejet refusé"
            )

    def _get_agent_row(self, clone_id: str) -> Any:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self._session_factory) as session:
            return AgentRepository(session).get(clone_id)

    def _get_configured_agent_row(self, agent_id: str) -> Any:
        """Distinct d'`_get_agent_row` ci-dessus (un clone OS-level réel) —
        vérifie l'existence d'un `apex.agent_registry.model.Agent` (passe
        « Agents V1 »), jamais le même vocabulaire."""
        from apex.db.engine import session_scope
        from apex.db.repositories import ConfiguredAgentRepository

        with session_scope(self._session_factory) as session:
            return ConfiguredAgentRepository(session).get(agent_id)

    def _record_event(self, mission: Mission, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.missions",
                msg=kind, context={"mission_id": mission.id, "kind": kind, **details},
            )

    def _record_audit(self, mission: Mission, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        # Écrit pour le compte de l'acteur réel (HUMAN/CORE) : jamais un clone,
        # même règle que AgentRuntime._record_audit (CORE écrit pour lui-même
        # via WRITE_AUDIT_LOG ; ici l'acteur porte déjà son propre droit
        # d'audit via l'action mission qu'il vient d'exercer).
        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=mission.id, details=details,
            )
