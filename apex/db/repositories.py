"""Accès aux données — une classe par agrégat, aucun SQL brut ailleurs dans le Core.

`AuditLogRepository` n'expose délibérément aucune méthode `update`/`delete` :
c'est une première ligne de défense au niveau Python, en plus de la
révocation `UPDATE`/`DELETE`/`TRUNCATE` appliquée par la migration au
niveau du rôle SQL (`docs/VISION_INTEGRATION.md` §3.3). Les deux sont
nécessaires : le rôle SQL est la garantie réelle, l'absence de méthode
évite qu'un appelant du Core soit même tenté d'essayer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from apex.config.schema import ComputePool
from apex.db.models import (
    AgentRow,
    AuditLogRow,
    ComputePoolRow,
    EventRow,
    MemoryRecordRow,
    MemoryRelationRow,
    MissionRow,
)


class ComputePoolRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, pool: ComputePool) -> ComputePoolRow:
        row = self._session.get(ComputePoolRow, pool.pool_id)
        if row is None:
            row = ComputePoolRow(pool_id=pool.pool_id)
            self._session.add(row)
        row.location = pool.location.value
        row.trust_level = pool.trust_level.value
        row.capabilities = [c.value for c in pool.capabilities]
        row.cpu_cores = pool.cpu_cores
        row.ram_gb = pool.ram_gb
        row.cost_per_hour = pool.cost_per_hour
        self._session.flush()
        return row

    def get(self, pool_id: str) -> ComputePoolRow | None:
        return self._session.get(ComputePoolRow, pool_id)

    def list_all(self) -> list[ComputePoolRow]:
        return list(self._session.scalars(select(ComputePoolRow).order_by(ComputePoolRow.pool_id)))


class EventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, *, ts: datetime, level: str, logger: str, msg: str, context: dict[str, Any]
    ) -> EventRow:
        row = EventRow(ts=ts, level=level, logger=logger, msg=msg, context=context)
        self._session.add(row)
        self._session.flush()
        return row

    def recent(self, limit: int = 100) -> list[EventRow]:
        stmt = select(EventRow).order_by(EventRow.ts.desc()).limit(limit)
        return list(self._session.scalars(stmt))


class AuditLogRepository:
    """INSERT seul — voir la note du module. Aucune méthode update/delete n'existe ici."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self, *, ts: datetime, actor: str, action: str, target: str | None, details: dict[str, Any]
    ) -> AuditLogRow:
        row = AuditLogRow(ts=ts, actor=actor, action=action, target=target, details=details)
        self._session.add(row)
        self._session.flush()
        return row

    def recent(self, limit: int = 100) -> list[AuditLogRow]:
        stmt = select(AuditLogRow).order_by(AuditLogRow.ts.desc()).limit(limit)
        return list(self._session.scalars(stmt))


class AgentRepository:
    """Persistance des clones. N'importe jamais `apex.agents` : ce module
    reste une couche d'infrastructure sous les modules de domaine, jamais
    l'inverse — d'où des méthodes en mots-clés plutôt qu'un objet `Clone`
    (même choix que `EventRepository.add`)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        clone_id: str,
        type: str,
        state: str,
        genome_ref: str,
        spawned_by: str,
        pool_id: str,
        isolation_level: str,
        mission_id: str | None,
        max_retries: int,
        created_at: datetime,
    ) -> AgentRow:
        row = AgentRow(
            clone_id=clone_id, type=type, state=state, genome_ref=genome_ref,
            spawned_by=spawned_by, pool_id=pool_id, isolation_level=isolation_level,
            mission_id=mission_id, max_retries=max_retries, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update_state(
        self,
        clone_id: str,
        state: str,
        *,
        started_at: datetime | None = None,
        terminated_at: datetime | None = None,
        last_heartbeat_at: datetime | None = None,
        retries_used: int | None = None,
        last_error: str | None = None,
    ) -> AgentRow | None:
        row = self._session.get(AgentRow, clone_id)
        if row is None:
            return None
        row.state = state
        if started_at is not None:
            row.started_at = started_at
        if terminated_at is not None:
            row.terminated_at = terminated_at
        if last_heartbeat_at is not None:
            row.last_heartbeat_at = last_heartbeat_at
        if retries_used is not None:
            row.retries_used = retries_used
        if last_error is not None:
            row.last_error = last_error
        self._session.flush()
        return row

    def touch_heartbeat(self, clone_id: str, ts: datetime) -> bool:
        """Étape 8 — écrit UNIQUEMENT `last_heartbeat_at`, jamais `state` (à
        la différence de `update_state`) : un battement de cœur ne doit
        jamais pouvoir, même par accident, écraser l'état réel d'un clone."""
        stmt = update(AgentRow).where(AgentRow.clone_id == clone_id).values(last_heartbeat_at=ts)
        result = self._session.execute(stmt)
        self._session.flush()
        return result.rowcount == 1

    def get(self, clone_id: str) -> AgentRow | None:
        return self._session.get(AgentRow, clone_id)

    def list_all(self, limit: int = 100) -> list[AgentRow]:
        stmt = select(AgentRow).order_by(AgentRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def list_active(self) -> list[AgentRow]:
        terminal = ("terminated", "failed")
        stmt = (
            select(AgentRow)
            .where(AgentRow.state.notin_(terminal))
            .order_by(AgentRow.created_at.desc())
        )
        return list(self._session.scalars(stmt))


class MissionRepository:
    """Persistance des missions. N'importe jamais `apex.missions` : même
    règle de couche que `AgentRepository` vis-à-vis d'`apex.agents` — les
    méthodes prennent des mots-clés, jamais un objet `Mission`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        title: str,
        description: str,
        specialty: str,
        objective: str,
        priority: str,
        constraints: str | None,
        assigned_agent: str | None,
        state: str,
        created_by: str,
        created_at: datetime,
        depends_on: tuple[str, ...] = (),
    ) -> MissionRow:
        row = MissionRow(
            id=id, title=title, description=description, specialty=specialty,
            objective=objective, priority=priority, constraints=constraints,
            assigned_agent=assigned_agent, state=state, result=None, error=None,
            validation_status=None, created_by=created_by, created_at=created_at,
            depends_on=list(depends_on),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update(
        self,
        mission_id: str,
        *,
        state: str | None = None,
        assigned_agent: str | None = None,
        result: str | None = None,
        error: str | None = None,
        validation_status: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        clear_validation_status: bool = False,
    ) -> MissionRow | None:
        row = self._session.get(MissionRow, mission_id)
        if row is None:
            return None
        if state is not None:
            row.state = state
        if assigned_agent is not None:
            row.assigned_agent = assigned_agent
        if result is not None:
            row.result = result
        if error is not None:
            row.error = error
        if clear_validation_status:
            row.validation_status = None
        elif validation_status is not None:
            row.validation_status = validation_status
        if started_at is not None:
            row.started_at = started_at
        if completed_at is not None:
            row.completed_at = completed_at
        self._session.flush()
        return row

    def get(self, mission_id: str) -> MissionRow | None:
        return self._session.get(MissionRow, mission_id)

    def list_all(self, limit: int = 100) -> list[MissionRow]:
        stmt = select(MissionRow).order_by(MissionRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def list_by_state(self, state: str, limit: int = 500) -> list[MissionRow]:
        """Étape 8 (Scheduler) — missions dans UN état donné, triées par
        ancienneté (le tri priorité/FIFO complet est appliqué côté domaine,
        `apex.missions.repository`, qui connaît `MissionPriority`, pas ici)."""
        stmt = (
            select(MissionRow)
            .where(MissionRow.state == state)
            .order_by(MissionRow.created_at.asc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def count_by_state(self, state: str) -> int:
        """Étape 8 — compte RÉEL en base, jamais un compteur en mémoire
        potentiellement désynchronisé : c'est ce qui rend la borne
        `max_concurrent_missions` valable même entre plusieurs processus."""
        stmt = select(func.count()).select_from(MissionRow).where(MissionRow.state == state)
        return int(self._session.scalar(stmt) or 0)

    def dependency_graph(self) -> dict[str, tuple[str, ...]]:
        """Paires (id, depends_on) de TOUTES les missions — utilisé
        uniquement pour la détection de cycle à la création
        (`apex.missions.dependencies`). Le volume de missions reste modeste
        à ce stade du projet (aucune pagination ailleurs dans ce module non
        plus) ; à revoir si un besoin réel de mise à l'échelle apparaît."""
        stmt = select(MissionRow.id, MissionRow.depends_on)
        return {row.id: tuple(row.depends_on or ()) for row in self._session.execute(stmt)}

    def claim_running(self, mission_id: str, *, started_at: datetime) -> bool:
        """Réclamation ATOMIQUE QUEUED -> RUNNING (étape 8) : un seul UPDATE
        conditionnel (`WHERE state = 'queued'`), jamais un lire-puis-écrire.
        C'est ce qui garantit qu'une mission ne peut jamais être réclamée
        deux fois, y compris entre plusieurs workers ou plusieurs instances
        du Scheduler qui partagent la même base — le SGBD est le seul
        arbitre, pas un verrou applicatif qui ne vaudrait que pour un
        processus. Retourne `True` si CETTE réclamation a réussi, `False`
        si la mission n'était déjà plus QUEUED (course perdue, ou absente)."""
        stmt = (
            update(MissionRow)
            .where(MissionRow.id == mission_id, MissionRow.state == "queued")
            .values(state="running", started_at=started_at)
        )
        result = self._session.execute(stmt)
        self._session.flush()
        return result.rowcount == 1


class MemoryRepository:
    """Persistance de `apex.memory` (étape 9) — INSERT seul, à l'image
    d'`AuditLogRepository` : aucune méthode `update`/`delete` n'existe ici,
    ni pour `memory_records` ni pour `memory_relations`. Une révision ou une
    relation nouvelle est toujours une NOUVELLE ligne, jamais une mutation —
    la garantie « pas de modification destructive » vient de l'ABSENCE de
    méthode, pas d'une discipline à respecter en appelant ce module."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- records -----------------------------------------------------------

    def create_record(
        self,
        *,
        id: str,
        kind: str,
        domain: str | None,
        content: str,
        source: str,
        created_by: str,
        confidence: float,
        verification_status: str,
        visibility: str,
        evidence_for: tuple[str, ...],
        evidence_against: tuple[str, ...],
        external_provenance: bool,
        external_source: str | None,
        supersedes_id: str | None,
        expires_at: datetime | None,
        created_at: datetime,
    ) -> MemoryRecordRow:
        row = MemoryRecordRow(
            id=id, kind=kind, domain=domain, content=content, source=source, created_by=created_by,
            confidence=confidence, verification_status=verification_status, visibility=visibility,
            evidence_for=list(evidence_for), evidence_against=list(evidence_against),
            external_provenance=external_provenance, external_source=external_source,
            supersedes_id=supersedes_id, expires_at=expires_at, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def promote_to_shared(self, record_id: str) -> bool:
        """Seule mutation autorisée sur `memory_records` : la VISIBILITÉ
        (métadonnée d'accès), jamais le contenu/la provenance/la confiance
        de la connaissance elle-même — voir `apex.memory.model.MemoryRecord`
        pour la distinction entre révision (nouvelle ligne) et promotion
        (changement de portée sur la ligne existante)."""
        stmt = (
            update(MemoryRecordRow)
            .where(MemoryRecordRow.id == record_id, MemoryRecordRow.visibility == "own")
            .values(visibility="shared")
        )
        result = self._session.execute(stmt)
        self._session.flush()
        return result.rowcount == 1

    def get_record(self, record_id: str) -> MemoryRecordRow | None:
        return self._session.get(MemoryRecordRow, record_id)

    def list_records(self, *, visibility: str | None = None, limit: int = 200) -> list[MemoryRecordRow]:
        stmt = select(MemoryRecordRow)
        if visibility is not None:
            stmt = stmt.where(MemoryRecordRow.visibility == visibility)
        stmt = stmt.order_by(MemoryRecordRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def find_successor(self, record_id: str) -> MemoryRecordRow | None:
        """Existe-t-il déjà une révision de `record_id` ? Utilisé pour
        garantir une chaîne de révisions linéaire (`apex.memory.service`
        refuse de réviser un enregistrement déjà révisé)."""
        stmt = select(MemoryRecordRow).where(MemoryRecordRow.supersedes_id == record_id).limit(1)
        return self._session.scalars(stmt).first()

    # -- relations -----------------------------------------------------------

    def create_relation(
        self,
        *,
        id: str,
        source_id: str,
        target_id: str,
        relation: str,
        provenance: str,
        created_by: str,
        confidence: float | None,
        created_at: datetime,
    ) -> MemoryRelationRow:
        row = MemoryRelationRow(
            id=id, source_id=source_id, target_id=target_id, relation=relation, provenance=provenance,
            created_by=created_by, confidence=confidence, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_relations_for(self, record_id: str, limit: int = 200) -> list[MemoryRelationRow]:
        stmt = (
            select(MemoryRelationRow)
            .where((MemoryRelationRow.source_id == record_id) | (MemoryRelationRow.target_id == record_id))
            .order_by(MemoryRelationRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))
