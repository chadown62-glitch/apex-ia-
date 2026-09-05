"""TaskBankService — écriture/lecture de la banque de tâches d'évaluation.

Registre : `TaskBank`, phase 5 — premier véritable laboratoire expérimental
d'APEX (voir `docs/PHASE_5_REPORT.md`). Chaque méthode qui a un effet de
bord passe par `PolicyEngine` en premier — même discipline que
`apex.missions.service.MissionService`. `Action.CREATE_TASK` n'est accordée
qu'à `Subject.HUMAN` (`apex.policy.engine.DEFAULT_ALLOWLIST`) : un clone ne
peut jamais écrire sa propre tâche d'évaluation, ce qui serait un
contournement trivial du split HELDOUT (un clone pourrait sinon s'auto-
attribuer des tâches faciles, ou simplement lire le contenu d'une tâche
HELDOUT en la créant lui-même).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.policy.engine import PolicyEngine
from apex.policy.model import Action, Subject
from apex.specialties.registry import by_id as specialty_by_id
from apex.tasks.model import Task, TaskSplit
from apex.tasks.repository import TaskRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskBankService:
    def __init__(self, session_factory: Any, *, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self._session_factory = session_factory
        self._repo = TaskRepository(session_factory)

    def create(
        self,
        *,
        split: TaskSplit,
        specialty: str,
        title: str,
        description: str,
        command: tuple[str, ...],
        created_by: Subject,
        dataset_ref: str | None = None,
        expected_answer: str | None = None,
    ) -> Task:
        self.policy.authorize(created_by, Action.CREATE_TASK)

        if not title.strip():
            raise ValueError("le titre de la tâche est obligatoire")
        if not description.strip():
            raise ValueError("la description de la tâche est obligatoire")
        command = tuple(command)
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("la commande de la tâche doit être un argv non vide (tuple de chaînes)")

        spec = specialty_by_id(specialty)
        if spec is None:
            raise ValueError(f"spécialité « {specialty} » inconnue")
        if not spec.is_supported:
            raise ValueError(
                f"spécialité « {specialty} » est PLANNED — aucune tâche ne peut lui être "
                "assignée pour l'instant"
            )

        task = Task.new(
            split=split, specialty=specialty, title=title, description=description,
            command=command, created_by=created_by.value, dataset_ref=dataset_ref,
            expected_answer=expected_answer,
        )
        self._repo.create(task)
        self._record_event(task, "task.created", {})
        self._record_audit(
            task, actor=created_by.value, action="task.created",
            details={"specialty": specialty, "split": split.value},
        )
        return task

    # -- lecture -------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        return self._repo.get(task_id)

    def list_all(self, limit: int = 200) -> list[Task]:
        return self._repo.list_all(limit=limit)

    def list_by_split(self, split: TaskSplit, limit: int = 200) -> list[Task]:
        return self._repo.list_by_split(split.value, limit=limit)

    def list_by_specialty(self, specialty: str, limit: int = 200) -> list[Task]:
        return self._repo.list_by_specialty(specialty, limit=limit)

    # -- internes -----------------------------------------------------------

    def _record_event(self, task: Task, kind: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(
                ts=_utcnow(), level="INFO", logger="apex.tasks",
                msg=kind, context={"task_id": task.id, "kind": kind, **details},
            )

    def _record_audit(self, task: Task, *, actor: str, action: str, details: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository

        # Écrit pour le compte de l'acteur réel (HUMAN seul, cf. docstring
        # du module) — même patron que MissionService._record_audit.
        self.policy.authorize(Subject.CORE, Action.WRITE_AUDIT_LOG)
        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=_utcnow(), actor=actor, action=action, target=task.id, details=details,
            )
