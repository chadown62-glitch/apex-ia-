"""Modèles SQLAlchemy — schéma déclaratif, indépendant du moteur.

Portée exacte du brief phase 2 (`docs/VISION_INTEGRATION.md` §5) :

- `ComputePool` -> table `compute_pools`, avec `trust_level` (principe 6).
- `EventSink` (stub -> implémenté) -> table `events`.
- `audit_log` -> écriture seule pour les rôles applicatifs ; la révocation
  `UPDATE`/`DELETE`/`TRUNCATE` est appliquée en migration (PostgreSQL), pas
  ici. Ce module ne fait que décrire la forme des données.

- `AgentRuntime` (registre : PLANNED -> STUB) -> table `agents`, ajoutée
  quand l'abstraction a cessé d'être seulement planifiée
  (`docs/COGNITIVE_ARCHITECTURE.md` §9 la nommait déjà comme table future).
- Mission Engine (étape 6) -> table `missions`, ajoutée maintenant que
  `apex.missions` existe réellement (ADR-0007 : jamais avant).
- `MemoryProvider` (registre : PLANNED -> STUB, étape 9) -> tables
  `memory_records`/`memory_relations`, tranche minimale (provenance,
  confiance, statut de vérification, révisions par `supersedes_id`,
  relations SUPPORTS/CONTRADICTS) — pas de RAG/embeddings (`EmbeddingProvider`
  reste PLANNED), pas de graphe visuel ni de moteur de traversée.
- `TaskBank`/`EvalRunner` (registre : PLANNED -> STUB, phase 5) -> tables
  `tasks`/`eval_runs`. Les deux sont **INSERT seul** : aucune méthode de
  modification n'existe côté `apex/db/repositories.py`, et `UPDATE` est
  révoqué en migration pour tous les rôles applicatifs (même garantie que
  `audit_log`) — une tâche ou un résultat d'évaluation ne change jamais
  silencieusement après coup ; une correction est une nouvelle ligne, pas
  une mutation. `eval_runs` conserve les échecs et les succès de façon
  identique (aucun filtrage à l'écriture) — un résultat négatif est une
  donnée, pas une erreur à effacer. Le rôle `apex_eval` (réservé sans
  usage depuis la migration initiale) reçoit `SELECT, INSERT` sur
  `eval_runs` mais **aucun accès** à `tasks` : c'est le mécanisme qui rend
  le critère HELDOUT (`docs/adr` ADR-0010, différé en phase 2) vrai par
  construction plutôt que par une règle applicative contournable.
- `SkillRegistry` (registre : PLANNED -> STUB, phase 14) -> table `skills`,
  seule table de ce module **mutable au sens strict** (hors `audit_log`
  écriture seule et la promotion de visibilité de `memory_records`) : un
  `UPDATE` ciblé (`SkillRepository.decide()`) fait passer `status` de
  `proposed` à `acquired`/`rejected`, UNE seule fois — jamais le contenu
  (nom/description/preuves). La généalogie (versionnement) n'est PAS une
  mutation : réviser une compétence insère une nouvelle ligne
  (`supersedes_id`), même patron que `memory_records`.
- `GenomeStore` (registre : PLANNED -> STUB, phase 17) -> table
  `genome_versions`, MÉTADONNÉES seules — le contenu du Genome est stocké
  par `ArtifactStoreProvider` (adressé par contenu, `content_hash` = le
  `genome_ref: hash` de `Clone`). Même discipline de mutation que `skills` :
  un `UPDATE` ciblé (`GenomeVersionRepository.decide()`) fait passer
  `status` de `proposed` à `promoted`/`rejected`, UNE seule fois.

Ce que ce module ne fait PAS :
- il ne crée aucune table pour des abstractions encore `PLANNED`
  (`model_runs`, `tool_usage`, `sandbox_execs` — ModelProvider,
  ToolProvider, SandboxProvider n'ont pas de table de persistance dédiée
  à ce stade). Créer leur schéma maintenant reviendrait à deviner une
  implémentation qui n'existe pas encore (ADR-0007 : rien n'est déclaré
  avant d'exister). Le partitionnement mensuel de ces tables reste un
  risque documenté (voir `docs/PHASE_2_REPORT.md`), à traiter par la phase
  qui introduit chaque abstraction.
- il ne partitionne rien lui-même : le partitionnement natif PostgreSQL de
  `events` est appliqué en DDL brut dans la migration Alembic, parce que
  SQLAlchemy n'a pas d'API portable pour `PARTITION BY RANGE`. Sur SQLite
  (tests), `events` reste une table simple.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ComputePoolRow(Base):
    """Persiste `apex.config.schema.ComputePool` (principe 6).

    Le schéma de configuration (`config.yaml`) reste la source de vérité au
    chargement ; cette table permet de l'historiser et de l'interroger sans
    reparser `config.yaml` à chaque fois (utile au Dashboard, phase 20).
    """

    __tablename__ = "compute_pools"

    pool_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    location: Mapped[str] = mapped_column(String(16), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ram_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "location": self.location,
            "trust_level": self.trust_level,
            "capabilities": list(self.capabilities),
            "cpu_cores": self.cpu_cores,
            "ram_gb": self.ram_gb,
            "cost_per_hour": self.cost_per_hour,
        }


class EventRow(Base):
    """Persistance de `EventSink` (registre : stub -> implémenté, phase 2).

    `ts` est la clé de partitionnement mensuel appliquée en migration
    PostgreSQL. Aucun secret n'est écrit tel quel : `context` doit avoir
    déjà traversé `apex.obs.logging.redact` avant d'atteindre ce modèle —
    voir `apex/db/event_sink.py`.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    logger: Mapped[str] = mapped_column(String(128), nullable=False)
    msg: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "level": self.level,
            "logger": self.logger,
            "msg": self.msg,
            "context": dict(self.context),
        }


class AuditLogRow(Base):
    """Journal d'audit — INSERT seul pour les rôles applicatifs.

    La garantie réelle n'est PAS portée par ce modèle : un rôle applicatif
    qui a une session SQLAlchemy peut techniquement construire un DELETE.
    La garantie est au niveau du SGBD (`REVOKE UPDATE, DELETE, TRUNCATE`
    dans la migration) — c'est délibéré : une politique non appliquée par
    le SGBD n'est pas une politique (`docs/VISION_INTEGRATION.md` §3.3).
    `apex/db/repositories.py` n'expose d'ailleurs aucune méthode de
    modification pour cette table, en plus de la contrainte SQL.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "details": dict(self.details),
        }


class AgentRow(Base):
    """Persistance d'un clone (`apex.agents.model.Clone`, `AgentRuntime`).

    `isolation_level` est une valeur CONSTATÉE (mesurée par le Gate si un
    rapport existe, `"unknown"` sinon) — cette colonne ne fait respecter
    aucune isolation réelle. L'isolation d'exécution effective dépend de
    `SandboxProvider` (PLANNED, phase 3), qui n'existe pas encore. Ne pas lire
    cette colonne comme une garantie de sécurité appliquée.

    Pas de contrainte `ForeignKey` vers `compute_pools.pool_id` : les deux
    tables ne sont pas encore synchronisées automatiquement (le pool est
    fourni à `AgentRuntime.spawn` comme objet de configuration, pas
    nécessairement déjà upserté en base) — documenté comme simplification,
    à revoir si un besoin réel de cohérence référentielle apparaît.
    """

    __tablename__ = "agents"

    clone_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    mission_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    genome_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    spawned_by: Mapped[str] = mapped_column(String(16), nullable=False)
    pool_id: Mapped[str] = mapped_column(String(64), nullable=False)
    isolation_level: Mapped[str] = mapped_column(String(32), nullable=False)
    retries_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clone_id": self.clone_id,
            "type": self.type,
            "state": self.state,
            "mission_id": self.mission_id,
            "genome_ref": self.genome_ref,
            "spawned_by": self.spawned_by,
            "pool_id": self.pool_id,
            "isolation_level": self.isolation_level,
            "retries_used": self.retries_used,
            "max_retries": self.max_retries,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "terminated_at": self.terminated_at.isoformat() if self.terminated_at else None,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
        }


class MissionRow(Base):
    """Persistance d'une Mission (`apex.missions.model.Mission`, Mission Engine).

    Pas de contrainte `ForeignKey` vers `agents.clone_id` : même simplification
    documentée que `AgentRow.pool_id` — `assigned_agent` référence un clone
    par convention, pas encore par contrainte référentielle. `specialty`
    référence un id de `apex.specialties.registry`, vérifié au niveau service
    (`apex.missions.service`), pas ici : cette table ne fait que décrire la
    forme des données, comme le reste de ce module.
    """

    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_agent: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Étape 8 — IDs d'autres missions devant être COMPLETED avant que
    #: celle-ci soit éligible au Scheduler. Fixé à la création, jamais muté
    #: ensuite (voir apex.missions.dependencies pour pourquoi ceci suffit à
    #: garantir l'absence de cycle par construction). Liste vide par défaut,
    #: jamais NULL, pour éviter un cas particulier côté lecture.
    depends_on: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "specialty": self.specialty,
            "objective": self.objective,
            "priority": self.priority,
            "constraints": self.constraints,
            "assigned_agent": self.assigned_agent,
            "state": self.state,
            "result": self.result,
            "error": self.error,
            "validation_status": self.validation_status,
            "created_by": self.created_by,
            "depends_on": list(self.depends_on or []),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class MemoryRecordRow(Base):
    """Persistance d'un `apex.memory.model.MemoryRecord` (étape 9).

    Jamais d'`UPDATE` en place depuis `apex.memory` : `apex/db/repositories.py`
    n'expose d'ailleurs aucune méthode de modification pour cette table, à
    l'image d'`AuditLogRepository` — une révision est une nouvelle ligne
    (`supersedes_id`), jamais une mutation. Pas de contrainte `ForeignKey`
    sur `supersedes_id` : même simplification documentée qu'`AgentRow.pool_id`
    et `MissionRow.assigned_agent` — l'existence est vérifiée au niveau
    service (`apex.memory.service`), pas ici.
    """

    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    evidence_for: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_against: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    external_provenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "domain": self.domain,
            "content": self.content,
            "source": self.source,
            "created_by": self.created_by,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
            "visibility": self.visibility,
            "evidence_for": list(self.evidence_for or []),
            "evidence_against": list(self.evidence_against or []),
            "external_provenance": self.external_provenance,
            "external_source": self.external_source,
            "supersedes_id": self.supersedes_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat(),
        }


class MemoryRelationRow(Base):
    """Persistance d'une `apex.memory.model.MemoryRelation` (étape 9) —
    SUPPORTS/CONTRADICTS entre deux `MemoryRecord`. Jamais mutée ni
    supprimée ; pas de `ForeignKey` (même simplification que ci-dessus),
    l'existence des deux extrémités est vérifiée au niveau service."""

    __tablename__ = "memory_relations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "provenance": self.provenance,
            "created_by": self.created_by,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
        }


class TaskRow(Base):
    """Persistance d'un `apex.tasks.model.Task` (`TaskBank`, phase 5).

    **INSERT seul** — aucune méthode de modification côté
    `apex/db/repositories.py`, `UPDATE`/`DELETE`/`TRUNCATE` révoqués en
    migration pour tous les rôles applicatifs : une tâche d'évaluation ne
    change jamais après coup, une correction est une nouvelle ligne. `split`
    (`train`/`heldout`) est le mécanisme du critère HELDOUT (ADR-0010) :
    combiné à l'absence totale de `GRANT` sur cette table pour le rôle
    `apex_eval`, un exécuteur d'évaluation ne peut structurellement pas lire
    les tâches HELDOUT, qu'il connaisse ou non leur `id`. `specialty`
    référence un id de `apex.specialties.registry`, vérifié au niveau
    service (`apex.tasks.service`), pas ici — même séparation que
    `MissionRow.specialty`. Pas de `ForeignKey` : même simplification
    documentée que `MissionRow.assigned_agent`.

    Volontairement **pas de champ `method`** ici : `docs/COGNITIVE_ARCHITECTURE.md`
    §5 est explicite — « chaque *run* est tagué par un champ fermé `method` »
    — c'est une propriété de la TENTATIVE (`EvalRunRow.method`), pas de
    l'énoncé de la tâche. Fixer un `method` sur `Task` empêcherait par
    construction de comparer plusieurs méthodes sur la même tâche, ce que
    §5 prévoit explicitement (« Méthode A vs B », comparaison future via
    `MetaLearner`).
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    split: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: Argv — liste de chaînes, JAMAIS une chaîne shell : même contrat que
    #: `apex.sandbox.model.SandboxSpec.command` ("un argv, jamais une
    #: fermeture Python"), qui la reçoit directement telle quelle
    #: (`apex.evalrunner.service`). Une chaîne shell obligerait à invoquer
    #: `sh -c`, une surface d'interprétation supplémentaire (quoting,
    #: expansion) qu'un simple argv évite par construction. Commande
    #: déterministe, exécutable telle quelle par SandboxProvider — jamais
    #: une tentative générée par un ModelProvider (hors périmètre phase 5,
    #: voir docs/PHASE_5_REPORT.md).
    command: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    dataset_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "split": self.split,
            "specialty": self.specialty,
            "title": self.title,
            "description": self.description,
            "command": list(self.command),
            "dataset_ref": self.dataset_ref,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


class EvalRunRow(Base):
    """Persistance d'un `apex.evalrunner.model.EvalRun` (`EvalRunner`, phase 5).

    **INSERT seul**, même garantie que `TaskRow` : un résultat d'évaluation
    — succès comme échec — n'est jamais modifié ni supprimé après coup.
    `oracle_verdict` (PASS/FAIL/ERROR/TIMEOUT/UNKNOWN,
    `apex.oracles.model.OracleVerdict`) est un jugement de correction de
    tâche, délibérément distinct de `sandbox_status`
    (`apex.sandbox.model.ExecutionStatus`, ex. TIMEOUT/OOM_KILLED) qui est
    un statut d'exécution processus : les deux vocabulaires ne sont jamais
    fusionnés, un run peut par ex. avoir `sandbox_status=SUCCESS` et
    `oracle_verdict=FAIL` (le code s'est exécuté sans erreur mais a produit
    une réponse incorrecte). `experiment_ref` est une étiquette libre,
    sans contrainte ni logique associée ici : une pure affordance de
    traçabilité pour permettre à une phase future (16, ExperimentEngine)
    de regrouper plusieurs `EvalRun` — Clones, seeds, `genome_ref` (déjà
    sur `AgentRow`/`Clone`) différents — sous une même expérience, comme
    donnée et non comme nouvelle abstraction. Pas de `ForeignKey` vers
    `tasks.id`/`agents.clone_id` : même simplification documentée que
    `MissionRow.assigned_agent` ; l'existence est vérifiée au niveau
    service (`apex.evalrunner.service`).
    """

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    clone_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dataset_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Étiquette libre de regroupement — voir docstring de la classe.
    experiment_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    oracle_verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    oracle_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "clone_id": self.clone_id,
            "method": self.method,
            "seed": self.seed,
            "dataset_ref": self.dataset_ref,
            "experiment_ref": self.experiment_ref,
            "oracle_verdict": self.oracle_verdict,
            "oracle_detail": self.oracle_detail,
            "sandbox_status": self.sandbox_status,
            "exit_code": self.exit_code,
            "elapsed_s": self.elapsed_s,
            "facts": dict(self.facts),
            "created_by": self.created_by,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat(),
        }


class SkillRow(Base):
    """Persistance d'un `apex.skills.model.Skill` (`SkillRegistry`, phase 14).

    Distincte du Genome (`docs/VISION_INTEGRATION.md` §2.2 : « une
    compétence a son propre cycle de promotion, ses benchmarks et sa
    généalogie ») : ce cycle de promotion est ICI, HUMAIN, jamais celui de
    `PromotionGovernor` (`PLANNED`, phase 16, qui gouverne la promotion vers
    le Core, un sujet entièrement différent).

    **Mutable, contrairement à `TaskRow`/`EvalRunRow`** : `status` (`proposed`
    -> `acquired`/`rejected`, UNE seule décision, jamais redécidée — même
    garde que `MissionService._require_no_prior_decision`) et les champs de
    décision associés sont les SEULES mutations autorisées ; `UPDATE` reste
    accordé à `apex_core` en migration mais `apex/db/repositories.py`
    n'expose qu'une méthode `decide()` ciblée, jamais une modification libre
    du contenu (nom/description/preuves). La « généalogie » (versionnement)
    n'est PAS une mutation : réviser une compétence insère une NOUVELLE ligne
    (`supersedes_id`), exactement le patron déjà prouvé par
    `MemoryRecordRow.supersedes_id` — jamais un historique réécrit.

    Pas de `ForeignKey` sur `prerequisite_skill_ids`/`evidence_run_ids`/
    `supersedes_id` : même simplification documentée que
    `MissionRow.assigned_agent` — l'existence est vérifiée au niveau service
    (`apex.skills.service`), pas ici.
    """

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Graphe de prérequis — IDs d'autres `Skill` devant être `acquired`
    #: avant que celle-ci puisse l'être. Fixé à la création, jamais muté
    #: ensuite — même invariant que `MissionRow.depends_on` (voir
    #: `apex.missions.dependencies`, réutilisé tel quel pour la détection de
    #: cycle, aucune duplication de cette logique).
    prerequisite_skill_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: IDs d'`EvalRun` (Phase 5) servant de preuve — vérifiés existants à la
    #: proposition (`apex.skills.service`) : jamais une preuve inventée.
    evidence_run_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    #: Confiance éventuellement fournie par l'appelant (ex. déjà calculée
    #: via `apex.calibration.ConfidenceCalibrator`) — jamais calculée ici,
    #: jamais un nombre décoratif fabriqué faute de mieux.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "specialty": self.specialty,
            "description": self.description,
            "status": self.status,
            "prerequisite_skill_ids": list(self.prerequisite_skill_ids or []),
            "evidence_run_ids": list(self.evidence_run_ids or []),
            "confidence": self.confidence,
            "supersedes_id": self.supersedes_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_reason": self.decision_reason,
        }


class GenomeVersionRow(Base):
    """Persistance des MÉTADONNÉES d'un `apex.genome.model.GenomeVersion`
    (`GenomeStore`, phase 17) — jamais le contenu lui-même.

    Le contenu (« données versionnées mutables, jamais de code »,
    `docs/COGNITIVE_ARCHITECTURE.md` §1) est stocké par `ArtifactStoreProvider`
    (STUB réel depuis la Phase 10) sous forme de JSON canonique — `content_hash`
    est le SEUL lien entre cette ligne et le contenu réel, exactement le
    `genome_ref: hash` déjà spécifié pour `Clone` (§2.2). Aucune duplication
    du mécanisme de stockage adressé par contenu : `GenomeStore` compose
    `ArtifactStore`, ne le réimplémente pas.

    **Mutable comme `SkillRow`, même discipline** : `status` (`proposed` ->
    `promoted`/`rejected`, UNE seule décision, jamais redécidée) est la
    SEULE mutation autorisée (`GenomeVersionRepository.decide()`, ciblé).
    Cycle de décision **HUMAIN**, provisoire en attendant `PromotionGovernor`
    (`PLANNED`, phase 16, périmètre jugé insuffisamment défini — voir
    `apex/interfaces/registry.py`) — même patron que `SkillRow`, jamais deux
    mécanismes de promotion concurrents.

    `parent_version_id`/`parent_version_id_b` portent la généalogie : un
    seul parent pour une mutation, deux pour un croisement
    (`apex.genome.mutation`) — jamais une réécriture, une nouvelle ligne à
    chaque proposition. `origin` documente comment cette version a été
    produite (`manual`/`mutation`/`crossover`), une donnée de traçabilité,
    jamais interprétée par ce module.
    """

    __tablename__ = "genome_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_version_id_b: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content_hash": self.content_hash,
            "parent_version_id": self.parent_version_id,
            "parent_version_id_b": self.parent_version_id_b,
            "origin": self.origin,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_reason": self.decision_reason,
        }
