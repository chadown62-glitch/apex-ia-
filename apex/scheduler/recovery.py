"""RecoveryService — réconciliation au démarrage du processus APEX.

`ExecutionRegistry` (apex.scheduler.registry) est TOUJOURS vide à
l'instanciation d'un nouveau processus — garantie structurelle, pas une
hypothèse : aucune poignée en mémoire ne survit à un redémarrage. Toute
ligne `missions.state = 'running'`, ou tout `agents.state` non terminal,
trouvée en base à cet instant est donc, PAR CONSTRUCTION, orpheline : son
exécution réelle (sous-processus, cgroups) a disparu avec le processus
précédent, ou n'a jamais pu être supervisée par CE processus.

Ne tente JAMAIS de reprendre une mission interrompue : reprendre
supposerait de retrouver un sous-processus dont plus personne n'a la
supervision (un PID hôte, dans un espace de noms, potentiellement déjà
réutilisé par autre chose — le vérifier serait dangereux, pas honnête).
Seule une nouvelle tentative, explicitement relancée par un humain ou par
une future mission, est possible ensuite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.agents.model import Clone, CloneState
from apex.agents.runtime import AgentRuntime
from apex.missions.service import MissionService
from apex.scheduler.model import RecoveryReport

_ORPHAN_REASON = "orphelin après redémarrage du processus — aucune reprise automatique, nouvelle tentative possible"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryService:
    def __init__(
        self, session_factory: Any, *, mission_service: MissionService, agent_runtime: AgentRuntime,
    ) -> None:
        self._session_factory = session_factory
        self._mission_service = mission_service
        self._agent_runtime = agent_runtime

    def reconcile_on_startup(self) -> RecoveryReport:
        orphaned_mission_ids = self._recover_orphaned_missions()
        orphaned_clone_ids = self._recover_orphaned_clones()
        report = RecoveryReport(
            orphaned_mission_ids=tuple(orphaned_mission_ids),
            orphaned_clone_ids=tuple(orphaned_clone_ids),
            ran_at=_utcnow(),
        )
        # Trace explicite TOUJOURS écrite, même si rien n'a été trouvé —
        # l'absence d'orphelin est aussi un fait à tracer (consigne étape 8).
        self._record_audit(report)
        return report

    def _recover_orphaned_missions(self) -> list[str]:
        running = self._mission_service.list_running()
        recovered: list[str] = []
        for mission in running:
            self._mission_service.mark_failed(mission.id, error=_ORPHAN_REASON)
            recovered.append(mission.id)
        return recovered

    def _recover_orphaned_clones(self) -> list[str]:
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self._session_factory) as session:
            active_rows = AgentRepository(session).list_active()
            clones = [Clone.from_row(row) for row in active_rows]

        recovered: list[str] = []
        for clone in clones:
            if clone.state is CloneState.READY:
                # READY est une attente LÉGITIME d'un travail, pas la trace
                # d'une exécution interrompue — `list_active()` l'inclut
                # (il n'exclut que TERMINATED/FAILED), mais ce n'est pas un
                # orphelin : aucun processus ne lui correspondait.
                continue
            self._agent_runtime.recover_orphan(clone, reason=_ORPHAN_REASON)
            recovered.append(clone.clone_id)
        return recovered

    def _record_audit(self, report: RecoveryReport) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import AuditLogRepository
        from apex.policy.model import Subject

        with session_scope(self._session_factory) as session:
            AuditLogRepository(session).record(
                ts=report.ran_at, actor=Subject.CORE.value, action="scheduler.recovery_reconciled",
                target=None,
                details={
                    "orphaned_mission_ids": list(report.orphaned_mission_ids),
                    "orphaned_clone_ids": list(report.orphaned_clone_ids),
                },
            )
