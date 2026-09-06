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

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from apex.config.schema import ComputePool
from apex.db.models import (
    AgentExecutionRow,
    AgentRow,
    AgentTeamRow,
    AgentTemplateRow,
    AuditLogRow,
    ComputePoolRow,
    ConfiguredAgentRow,
    ConversationRow,
    EvalRunRow,
    EventRow,
    GenomeVersionRow,
    LearningSessionRow,
    MemoryRecordRow,
    MemoryRelationRow,
    MessageRow,
    MissionRow,
    PopulationMemberRow,
    ProjectRow,
    SkillRow,
    TaskRow,
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
        agent_id: str | None = None,
    ) -> MissionRow:
        row = MissionRow(
            id=id, title=title, description=description, specialty=specialty,
            objective=objective, priority=priority, constraints=constraints,
            assigned_agent=assigned_agent, agent_id=agent_id, state=state, result=None, error=None,
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
        agent_id: str | None = None,
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
        if agent_id is not None:
            row.agent_id = agent_id
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


class TaskRepository:
    """Persistance de `apex.tasks` (`TaskBank`, phase 5) — INSERT seul, même
    garantie que `MemoryRepository` : aucune méthode `update`/`delete`
    n'existe ici. Une tâche ne change jamais après coup."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        split: str,
        specialty: str,
        title: str,
        description: str,
        command: tuple[str, ...],
        dataset_ref: str | None,
        created_by: str,
        created_at: datetime,
        expected_answer: str | None = None,
    ) -> TaskRow:
        row = TaskRow(
            id=id, split=split, specialty=specialty, title=title, description=description,
            command=list(command), dataset_ref=dataset_ref, created_by=created_by,
            expected_answer=expected_answer, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, task_id: str) -> TaskRow | None:
        return self._session.get(TaskRow, task_id)

    def list_all(self, limit: int = 200) -> list[TaskRow]:
        stmt = select(TaskRow).order_by(TaskRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def list_by_split(self, split: str, limit: int = 200) -> list[TaskRow]:
        stmt = (
            select(TaskRow)
            .where(TaskRow.split == split)
            .order_by(TaskRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_by_specialty(self, specialty: str, limit: int = 200) -> list[TaskRow]:
        """Phase 12 — `WeaknessDetectorService` : point d'entrée pour
        rassembler les tâches d'une compétence/domaine donné, avant de
        remonter leurs `EvalRun` associés."""
        stmt = (
            select(TaskRow)
            .where(TaskRow.specialty == specialty)
            .order_by(TaskRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))


class EvalRunRepository:
    """Persistance de `apex.evalrunner` (`EvalRunner`, phase 5) — INSERT
    seul, même garantie que `TaskRepository` : un résultat d'évaluation,
    positif ou négatif, n'est jamais modifié ni supprimé après coup."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        task_id: str,
        clone_id: str | None,
        method: str,
        seed: int | None,
        dataset_ref: str | None,
        experiment_ref: str | None,
        oracle_verdict: str,
        oracle_detail: str | None,
        sandbox_status: str | None,
        exit_code: int | None,
        elapsed_s: float | None,
        facts: dict[str, Any],
        created_by: str,
        started_at: datetime | None,
        completed_at: datetime | None,
        created_at: datetime,
        model_id: str | None = None,
    ) -> EvalRunRow:
        row = EvalRunRow(
            id=id, task_id=task_id, clone_id=clone_id, method=method, seed=seed,
            dataset_ref=dataset_ref, experiment_ref=experiment_ref, oracle_verdict=oracle_verdict,
            oracle_detail=oracle_detail, sandbox_status=sandbox_status, exit_code=exit_code,
            elapsed_s=elapsed_s, model_id=model_id, facts=facts, created_by=created_by,
            started_at=started_at, completed_at=completed_at, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, eval_run_id: str) -> EvalRunRow | None:
        return self._session.get(EvalRunRow, eval_run_id)

    def list_all(self, limit: int = 200) -> list[EvalRunRow]:
        stmt = select(EvalRunRow).order_by(EvalRunRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def list_by_task(self, task_id: str, limit: int = 200) -> list[EvalRunRow]:
        stmt = (
            select(EvalRunRow)
            .where(EvalRunRow.task_id == task_id)
            .order_by(EvalRunRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_by_clone(self, clone_id: str, limit: int = 200) -> list[EvalRunRow]:
        stmt = (
            select(EvalRunRow)
            .where(EvalRunRow.clone_id == clone_id)
            .order_by(EvalRunRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_by_experiment_ref_prefix(self, prefix: str, limit: int = 200) -> list[EvalRunRow]:
        """`experiment_ref` est déjà une colonne indexée (voir la migration
        `e7f9a1c3b5d7`) — aucun nouvel index requis. Utilisé par
        `apex.metalearner.autonomy.AutonomousLearningService.history` pour
        retrouver les `EvalRun` produits par un cycle autonome, jamais pour
        les runs déclenchés directement via `POST /experiment-cycles`
        (`experiment_ref=None` dans ce cas)."""
        stmt = (
            select(EvalRunRow)
            .where(EvalRunRow.experiment_ref.like(f"{prefix}%"))
            .order_by(EvalRunRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))


class SkillRepository:
    """Persistance de `apex.skills` (`SkillRegistry`, phase 14). N'importe
    jamais `apex.skills` : même règle de couche qu'`AgentRepository`/
    `MissionRepository` — méthodes en mots-clés, jamais un objet `Skill`.

    `decide()` est la SEULE méthode de mutation — un `UPDATE` ciblé sur les
    seuls champs de décision, jamais un `update` générique du contenu de la
    compétence (même discipline que `AgentRepository.touch_heartbeat` :
    l'absence de méthode générale est la garde-fou Python, en plus de la
    garantie SQL)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        name: str,
        specialty: str,
        description: str,
        status: str,
        prerequisite_skill_ids: tuple[str, ...],
        evidence_run_ids: tuple[str, ...],
        confidence: float | None,
        supersedes_id: str | None,
        created_by: str,
        created_at: datetime,
    ) -> SkillRow:
        row = SkillRow(
            id=id, name=name, specialty=specialty, description=description, status=status,
            prerequisite_skill_ids=list(prerequisite_skill_ids),
            evidence_run_ids=list(evidence_run_ids), confidence=confidence,
            supersedes_id=supersedes_id, created_by=created_by, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def decide(
        self,
        skill_id: str,
        *,
        status: str,
        decided_by: str,
        decided_at: datetime,
        decision_reason: str | None,
    ) -> SkillRow | None:
        """Réclamation conditionnelle `WHERE status = 'proposed'` — même
        motif que `MissionRepository.claim_running` : garantit qu'une
        compétence ne peut jamais être décidée deux fois, y compris en cas
        d'appels concurrents. Retourne `None` si déjà décidée ou absente."""
        stmt = (
            update(SkillRow)
            .where(SkillRow.id == skill_id, SkillRow.status == "proposed")
            .values(status=status, decided_by=decided_by, decided_at=decided_at, decision_reason=decision_reason)
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(SkillRow, skill_id)

    def get(self, skill_id: str) -> SkillRow | None:
        return self._session.get(SkillRow, skill_id)

    def list_all(self, limit: int = 200) -> list[SkillRow]:
        stmt = select(SkillRow).order_by(SkillRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def list_by_specialty(self, specialty: str, limit: int = 200) -> list[SkillRow]:
        stmt = (
            select(SkillRow)
            .where(SkillRow.specialty == specialty)
            .order_by(SkillRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def find_successor(self, skill_id: str) -> SkillRow | None:
        """Existe-t-il déjà une révision de `skill_id` ? Même patron que
        `MemoryRepository.find_successor` — garantit une chaîne de
        révisions linéaire."""
        stmt = select(SkillRow).where(SkillRow.supersedes_id == skill_id).limit(1)
        return self._session.scalars(stmt).first()

    def dependency_graph(self) -> dict[str, tuple[str, ...]]:
        """Paires (id, prerequisite_skill_ids) de TOUTES les compétences —
        utilisé uniquement pour la détection de cycle à la proposition
        (`apex.skills.service`, réutilise `apex.missions.dependencies.
        detect_cycle` tel quel). Même limitation de volume assumée que
        `MissionRepository.dependency_graph`."""
        stmt = select(SkillRow.id, SkillRow.prerequisite_skill_ids)
        return {row.id: tuple(row.prerequisite_skill_ids or ()) for row in self._session.execute(stmt)}


class GenomeVersionRepository:
    """Persistance de `apex.genome` (`GenomeStore`, phase 17). N'importe
    jamais `apex.genome` : même règle de couche qu'`AgentRepository`/
    `SkillRepository` — méthodes en mots-clés, jamais un objet
    `GenomeVersion`. Ne stocke JAMAIS le contenu du Genome lui-même
    (`ArtifactStoreProvider` s'en charge) — seulement `content_hash` et la
    généalogie.

    `decide()` est la SEULE méthode de mutation — même garde-fou Python que
    `SkillRepository.decide()` (réclamation conditionnelle `WHERE status =
    'proposed'`, décision unique garantie même sous appels concurrents)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        content_hash: str,
        parent_version_id: str | None,
        parent_version_id_b: str | None,
        origin: str,
        status: str,
        created_by: str,
        created_at: datetime,
    ) -> GenomeVersionRow:
        row = GenomeVersionRow(
            id=id, content_hash=content_hash, parent_version_id=parent_version_id,
            parent_version_id_b=parent_version_id_b, origin=origin, status=status,
            created_by=created_by, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def decide(
        self,
        version_id: str,
        *,
        status: str,
        decided_by: str,
        decided_at: datetime,
        decision_reason: str | None,
    ) -> GenomeVersionRow | None:
        stmt = (
            update(GenomeVersionRow)
            .where(GenomeVersionRow.id == version_id, GenomeVersionRow.status == "proposed")
            .values(status=status, decided_by=decided_by, decided_at=decided_at, decision_reason=decision_reason)
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(GenomeVersionRow, version_id)

    def get(self, version_id: str) -> GenomeVersionRow | None:
        return self._session.get(GenomeVersionRow, version_id)

    def list_all(self, limit: int = 200) -> list[GenomeVersionRow]:
        stmt = select(GenomeVersionRow).order_by(GenomeVersionRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def get_current(self) -> GenomeVersionRow | None:
        """La version `promoted` la plus récemment décidée — jamais un
        pointeur mutable séparé (source d'incohérence possible), toujours
        dérivé des lignes de décision elles-mêmes."""
        stmt = (
            select(GenomeVersionRow)
            .where(GenomeVersionRow.status == "promoted")
            .order_by(GenomeVersionRow.decided_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()


class PopulationMemberRepository:
    """Persistance de `apex.metalearner.population_service` (passe « Real
    Clone Population + Evolution Loop »). N'importe jamais
    `apex.metalearner` : même règle de couche que `GenomeVersionRepository`
    — méthodes en mots-clés, jamais un objet `PopulationMember`.

    `get_by_experiment_ref` est LE mécanisme d'idempotence (mandat §17 de la
    mission) — vérifié par l'appelant AVANT toute création, jamais une
    contrainte d'unicité silencieusement avalée : la colonne porte bien
    `unique=True` (défense en profondeur), mais le domaine consulte toujours
    d'abord cette méthode pour renvoyer le membre existant plutôt que de
    heurter une erreur d'intégrité.

    `set_status`/`record_fitness` sont les SEULES méthodes de mutation —
    même discipline que `GenomeVersionRepository.decide()` : des colonnes
    ciblées, jamais une ligne entière réécrite."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        specialty: str,
        generation: int,
        clone_id: str,
        genome_version_id: str,
        parent_member_id: str | None,
        status: str,
        created_by: str,
        experiment_ref: str,
        created_at: datetime,
    ) -> PopulationMemberRow:
        row = PopulationMemberRow(
            id=id, specialty=specialty, generation=generation, clone_id=clone_id,
            genome_version_id=genome_version_id, parent_member_id=parent_member_id,
            status=status, created_by=created_by, experiment_ref=experiment_ref, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, member_id: str) -> PopulationMemberRow | None:
        return self._session.get(PopulationMemberRow, member_id)

    def get_by_experiment_ref(self, experiment_ref: str) -> PopulationMemberRow | None:
        stmt = select(PopulationMemberRow).where(PopulationMemberRow.experiment_ref == experiment_ref)
        return self._session.scalars(stmt).first()

    def list_by_specialty(self, specialty: str, *, limit: int = 500) -> list[PopulationMemberRow]:
        stmt = (
            select(PopulationMemberRow)
            .where(PopulationMemberRow.specialty == specialty)
            .order_by(PopulationMemberRow.generation.desc(), PopulationMemberRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def count_active(self, specialty: str) -> int:
        """Nombre de membres NON `retired` — le compteur utilisé par le
        plafond de population (§15 de la mission) : un membre retiré libère
        sa place, jamais supprimé (historique préservé)."""
        stmt = (
            select(func.count())
            .select_from(PopulationMemberRow)
            .where(PopulationMemberRow.specialty == specialty, PopulationMemberRow.status != "retired")
        )
        return int(self._session.scalar(stmt) or 0)

    def record_fitness(
        self, member_id: str, *, fitness: float, fitness_components: dict[str, Any],
        skill_candidate_id: str | None, genome_candidate_id: str | None,
    ) -> PopulationMemberRow | None:
        stmt = (
            update(PopulationMemberRow)
            .where(PopulationMemberRow.id == member_id)
            .values(
                fitness=fitness, fitness_components=fitness_components,
                skill_candidate_id=skill_candidate_id, genome_candidate_id=genome_candidate_id,
            )
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(PopulationMemberRow, member_id)

    def set_status(self, member_id: str, *, status: str, decided_at: datetime) -> PopulationMemberRow | None:
        row = self._session.get(PopulationMemberRow, member_id)
        if row is None or row.status == "retired":
            # `retired` est terminal — même garde qu'`AgentRuntime` sur les
            # transitions de `CloneState` : jamais de sortie hors machine
            # à états, même si l'appelant se trompe.
            return None
        stmt = (
            update(PopulationMemberRow)
            .where(PopulationMemberRow.id == member_id)
            .values(status=status, decided_at=decided_at)
        )
        self._session.execute(stmt)
        self._session.flush()
        return self._session.get(PopulationMemberRow, member_id)


class LearningSessionRepository:
    """Persistance de `apex.metalearner.learning_session_service` (passe
    « Autonomous Multi-Generation Learning Orchestrator »). N'importe
    jamais `apex.metalearner` : même règle de couche que
    `PopulationMemberRepository` — méthodes en mots-clés, jamais un objet
    `LearningSession`.

    `create` ne rattrape JAMAIS lui-même une violation de l'index UNIQUE
    partiel (`learning_sessions_one_running_per_specialty_idx`) — cette
    couche reste un CRUD fin, la traduction en `LearningSessionAlreadyRunning`
    vit dans la couche domaine (`apex.metalearner.learning_session_service`,
    seule à savoir quelle exception métier produire), même séparation que
    partout ailleurs dans ce module.

    `transition_to_running`/`record_generation`/`finish`/`request_cancel`
    sont toutes des `UPDATE ... WHERE status = ...` conditionnels — même
    discipline que `MissionRepository.claim_running`/`PopulationMemberRepository.
    set_status` : le SGBD est le seul arbitre, `rowcount != 1` signifie
    qu'un AUTRE appelant a déjà changé l'état entre-temps, jamais une
    erreur, juste `None`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        specialty: str,
        status: str,
        max_generations: int,
        convergence_patience: int,
        created_by: str,
        created_at: datetime,
    ) -> LearningSessionRow:
        row = LearningSessionRow(
            id=id, specialty=specialty, status=status, max_generations=max_generations,
            convergence_patience=convergence_patience, created_by=created_by, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, session_id: str) -> LearningSessionRow | None:
        return self._session.get(LearningSessionRow, session_id)

    def list_by_specialty(self, specialty: str, *, limit: int = 50) -> list[LearningSessionRow]:
        stmt = (
            select(LearningSessionRow)
            .where(LearningSessionRow.specialty == specialty)
            .order_by(LearningSessionRow.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_all(self, *, limit: int = 50) -> list[LearningSessionRow]:
        stmt = select(LearningSessionRow).order_by(LearningSessionRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def transition_to_running(self, session_id: str, *, from_statuses: tuple[str, ...]) -> LearningSessionRow | None:
        """Utilisé par la reprise (`failed`/`cancelled` -> `running`) — la
        même violation d'index UNIQUE que `create` peut survenir ici si une
        AUTRE session de la même spécialité est déjà `running`, remontée
        telle quelle (rattrapée en couche domaine)."""
        stmt = (
            update(LearningSessionRow)
            .where(LearningSessionRow.id == session_id, LearningSessionRow.status.in_(from_statuses))
            .values(status="running")
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(LearningSessionRow, session_id)

    def record_generation(
        self,
        session_id: str,
        *,
        generations_completed: int,
        generations_since_improvement: int,
        best_member_id: str | None,
        best_fitness: float | None,
        best_genome_version_id: str | None,
    ) -> LearningSessionRow | None:
        # `cancel_requested` reste éligible ici, PAS seulement `running` :
        # une génération qui vient de se terminer a réellement produit des
        # membres/population (déjà persistés, jamais perdus) même si une
        # annulation a été demandée PENDANT son exécution — l'enregistrer
        # est correct, seule la décision de CONTINUER (tour de boucle
        # suivant) doit respecter l'annulation, jamais l'enregistrement
        # d'un travail déjà réellement accompli.
        stmt = (
            update(LearningSessionRow)
            .where(
                LearningSessionRow.id == session_id,
                LearningSessionRow.status.in_(("running", "cancel_requested")),
            )
            .values(
                generations_completed=generations_completed,
                generations_since_improvement=generations_since_improvement,
                best_member_id=best_member_id, best_fitness=best_fitness,
                best_genome_version_id=best_genome_version_id,
            )
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(LearningSessionRow, session_id)

    def finish(
        self, session_id: str, *, status: str, stop_reason: str, error: str | None, completed_at: datetime,
    ) -> LearningSessionRow | None:
        stmt = (
            update(LearningSessionRow)
            .where(LearningSessionRow.id == session_id, LearningSessionRow.status.in_(("running", "cancel_requested")))
            .values(status=status, stop_reason=stop_reason, error=error, completed_at=completed_at)
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(LearningSessionRow, session_id)

    def request_cancel(self, session_id: str) -> LearningSessionRow | None:
        stmt = (
            update(LearningSessionRow)
            .where(LearningSessionRow.id == session_id, LearningSessionRow.status == "running")
            .values(status="cancel_requested")
        )
        result = self._session.execute(stmt)
        self._session.flush()
        if result.rowcount != 1:
            return None
        return self._session.get(LearningSessionRow, session_id)


class ProjectRepository:
    """Persistance de `apex.projects` (passe « Première interface
    utilisable »). N'importe jamais `apex.projects` : même règle de couche
    que `MissionRepository` — méthodes en mots-clés, jamais un objet
    `Project`. Aucune méthode de suppression : un projet regroupant déjà des
    conversations, le supprimer silencieusement orphelinerait leur
    `project_id` — hors scope MVP, à traiter si le besoin réel apparaît."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, id: str, name: str, description: str, created_by: str, created_at: datetime) -> ProjectRow:
        row = ProjectRow(id=id, name=name, description=description, created_by=created_by, created_at=created_at)
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, project_id: str) -> ProjectRow | None:
        return self._session.get(ProjectRow, project_id)

    def list_all(self, limit: int = 200) -> list[ProjectRow]:
        stmt = select(ProjectRow).order_by(ProjectRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))


class ConversationRepository:
    """Persistance de `apex.conversations.model.Conversation`. N'importe
    jamais `apex.conversations` — même règle de couche que
    `MissionRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, *, id: str, title: str, project_id: str | None, created_by: str, created_at: datetime,
    ) -> ConversationRow:
        row = ConversationRow(
            id=id, title=title, project_id=project_id, created_by=created_by,
            created_at=created_at, updated_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def rename(self, conversation_id: str, *, title: str, updated_at: datetime) -> ConversationRow | None:
        row = self._session.get(ConversationRow, conversation_id)
        if row is None:
            return None
        row.title = title
        row.updated_at = updated_at
        self._session.flush()
        return row

    def touch(self, conversation_id: str, *, updated_at: datetime) -> ConversationRow | None:
        """Avance `updated_at` sans autre changement — appelé après chaque
        message réellement enregistré (`ConversationService.send_message`),
        pour que « conversations récentes » (tri par `updated_at`) reflète
        l'activité réelle, jamais seulement la date de création."""
        row = self._session.get(ConversationRow, conversation_id)
        if row is None:
            return None
        row.updated_at = updated_at
        self._session.flush()
        return row

    def get(self, conversation_id: str) -> ConversationRow | None:
        return self._session.get(ConversationRow, conversation_id)

    def list_all(self, limit: int = 200) -> list[ConversationRow]:
        stmt = select(ConversationRow).order_by(ConversationRow.updated_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def delete(self, conversation_id: str) -> bool:
        row = self._session.get(ConversationRow, conversation_id)
        if row is None:
            return False
        self._session.delete(row)
        # Les messages de cette conversation n'ont pas de ForeignKey
        # (même simplification documentée que `MissionRow.assigned_agent`) —
        # supprimés explicitement ici, jamais laissés orphelins en base.
        self._session.execute(sa_delete(MessageRow).where(MessageRow.conversation_id == conversation_id))
        self._session.flush()
        return True


class MessageRepository:
    """Persistance de `apex.conversations.model.Message`. INSERT seul, comme
    `EventRepository`/`AuditLogRepository` — un message envoyé n'est jamais
    réécrit après coup, une correction serait un nouveau message."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        conversation_id: str,
        role: str,
        content: str,
        model_id: str | None,
        provider_id: str | None,
        status: str,
        error: str | None,
        created_at: datetime,
    ) -> MessageRow:
        row = MessageRow(
            id=id, conversation_id=conversation_id, role=role, content=content,
            model_id=model_id, provider_id=provider_id, status=status, error=error, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_by_conversation(self, conversation_id: str, limit: int = 500) -> list[MessageRow]:
        stmt = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at.asc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))


class AgentTemplateRepository:
    """Persistance de `apex.agent_templates.model.AgentTemplate`. N'importe
    jamais `apex.agent_templates` — même règle de couche que
    `MissionRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        name: str,
        role: str,
        model_role: str | None,
        tools: tuple[str, ...],
        description: str,
        status: str,
        created_by: str,
        created_at: datetime,
    ) -> AgentTemplateRow:
        row = AgentTemplateRow(
            id=id, name=name, role=role, model_role=model_role, tools=list(tools),
            description=description, status=status, created_by=created_by, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, template_id: str) -> AgentTemplateRow | None:
        return self._session.get(AgentTemplateRow, template_id)

    def list_all(self, limit: int = 200) -> list[AgentTemplateRow]:
        stmt = select(AgentTemplateRow).order_by(AgentTemplateRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def delete(self, template_id: str) -> bool:
        row = self._session.get(AgentTemplateRow, template_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class ConfiguredAgentRepository:
    """Persistance de `apex.agent_registry.model.Agent` (passe « Agents
    V1 »). N'importe jamais `apex.agent_registry` — même règle de couche
    que `MissionRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        id: str,
        name: str,
        description: str,
        role: str,
        specialty: str | None,
        model_role: str | None,
        required_capabilities: tuple[str, ...],
        tools: tuple[str, ...],
        permissions: tuple[str, ...],
        instructions: str | None,
        enabled: bool,
        project_id: str | None,
        template_id: str | None,
        created_by: str,
        created_at: datetime,
    ) -> ConfiguredAgentRow:
        row = ConfiguredAgentRow(
            id=id, name=name, description=description, role=role, specialty=specialty,
            model_role=model_role, required_capabilities=list(required_capabilities),
            tools=list(tools), permissions=list(permissions), instructions=instructions,
            enabled=enabled, project_id=project_id, template_id=template_id,
            created_by=created_by, created_at=created_at, updated_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def update(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        model_role: str | None = None,
        required_capabilities: tuple[str, ...] | None = None,
        tools: tuple[str, ...] | None = None,
        permissions: tuple[str, ...] | None = None,
        instructions: str | None = None,
        enabled: bool | None = None,
        updated_at: datetime,
    ) -> ConfiguredAgentRow | None:
        row = self._session.get(ConfiguredAgentRow, agent_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if model_role is not None:
            row.model_role = model_role
        if required_capabilities is not None:
            row.required_capabilities = list(required_capabilities)
        if tools is not None:
            row.tools = list(tools)
        if permissions is not None:
            row.permissions = list(permissions)
        if instructions is not None:
            row.instructions = instructions
        if enabled is not None:
            row.enabled = enabled
        row.updated_at = updated_at
        self._session.flush()
        return row

    def get(self, agent_id: str) -> ConfiguredAgentRow | None:
        return self._session.get(ConfiguredAgentRow, agent_id)

    def list_all(self, limit: int = 200) -> list[ConfiguredAgentRow]:
        stmt = select(ConfiguredAgentRow).order_by(ConfiguredAgentRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))

    def delete(self, agent_id: str) -> bool:
        row = self._session.get(ConfiguredAgentRow, agent_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class AgentExecutionRepository:
    """Persistance de `apex.agent_registry.model.AgentExecution`. `complete`
    est le SEUL `UPDATE` — mêmes garanties que `SkillRepository.decide` :
    aucune méthode générale de modification n'existe, jamais un contenu
    déjà écrit qui change silencieusement."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, *, id: str, agent_id: str, mission_id: str, status: str, started_at: datetime,
    ) -> AgentExecutionRow:
        row = AgentExecutionRow(
            id=id, agent_id=agent_id, mission_id=mission_id, status=status,
            model_id=None, provider_id=None, result=None, error=None, fallback_reason=None,
            elapsed_s=None, tokens_prompt=None, tokens_completion=None,
            started_at=started_at, completed_at=None,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def complete(
        self,
        execution_id: str,
        *,
        status: str,
        model_id: str | None,
        provider_id: str | None,
        result: str | None,
        error: str | None,
        fallback_reason: str | None,
        completed_at: datetime,
        elapsed_s: float | None = None,
        tokens_prompt: int | None = None,
        tokens_completion: int | None = None,
    ) -> AgentExecutionRow | None:
        """`elapsed_s`/`tokens_prompt`/`tokens_completion` restent optionnels
        (défaut `None`) — passe « Adaptive Model Router V1 », strictement
        additifs : tout appelant existant qui ne les fournit pas continue de
        produire exactement la même ligne qu'avant (colonnes `NULL`)."""
        row = self._session.get(AgentExecutionRow, execution_id)
        if row is None:
            return None
        row.status = status
        row.model_id = model_id
        row.provider_id = provider_id
        row.result = result
        row.error = error
        row.fallback_reason = fallback_reason
        row.elapsed_s = elapsed_s
        row.tokens_prompt = tokens_prompt
        row.tokens_completion = tokens_completion
        row.completed_at = completed_at
        self._session.flush()
        return row

    def get(self, execution_id: str) -> AgentExecutionRow | None:
        return self._session.get(AgentExecutionRow, execution_id)

    def list_by_agent(self, agent_id: str, limit: int = 200) -> list[AgentExecutionRow]:
        stmt = (
            select(AgentExecutionRow)
            .where(AgentExecutionRow.agent_id == agent_id)
            .order_by(AgentExecutionRow.started_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_by_mission(self, mission_id: str, limit: int = 200) -> list[AgentExecutionRow]:
        stmt = (
            select(AgentExecutionRow)
            .where(AgentExecutionRow.mission_id == mission_id)
            .order_by(AgentExecutionRow.started_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def list_by_model(self, model_id: str, limit: int = 500) -> list[AgentExecutionRow]:
        """Passe « Adaptive Model Router V1 » — seule requête d'agrégation
        par `model_id` : jusqu'ici aucune méthode ne lisait cette colonne en
        filtre. Ordre déterministe (`started_at desc`, puis `id` pour
        départager les égalités exactes), jamais l'ordre d'itération
        implicite d'un résultat SQL non trié."""
        stmt = (
            select(AgentExecutionRow)
            .where(AgentExecutionRow.model_id == model_id)
            .order_by(AgentExecutionRow.started_at.desc(), AgentExecutionRow.id)
            .limit(limit)
        )
        return list(self._session.scalars(stmt))

    def latest_for_agent(self, agent_id: str) -> AgentExecutionRow | None:
        stmt = (
            select(AgentExecutionRow)
            .where(AgentExecutionRow.agent_id == agent_id)
            .order_by(AgentExecutionRow.started_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()


class AgentTeamRepository:
    """Persistance de `apex.agent_registry.model.AgentTeam` — structure V1
    minimale (voir `AgentTeamRow`)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, *, id: str, name: str, description: str, agent_ids: tuple[str, ...],
        created_by: str, created_at: datetime,
    ) -> AgentTeamRow:
        row = AgentTeamRow(
            id=id, name=name, description=description, agent_ids=list(agent_ids),
            created_by=created_by, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, team_id: str) -> AgentTeamRow | None:
        return self._session.get(AgentTeamRow, team_id)

    def list_all(self, limit: int = 200) -> list[AgentTeamRow]:
        stmt = select(AgentTeamRow).order_by(AgentTeamRow.created_at.desc()).limit(limit)
        return list(self._session.scalars(stmt))
