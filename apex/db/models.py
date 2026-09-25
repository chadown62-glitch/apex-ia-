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

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    #: Passe « Agents V1 » — référence un `ConfiguredAgentRow.id` (l'entité
    #: `Agent` réutilisable), délibérément DISTINCT d'`assigned_agent`
    #: ci-dessus (qui référence un `AgentRow.id`, un clone OS-level réel,
    #: validé par `MissionService.create` contre `compatible_agent_types`).
    #: Conflater les deux aurait cassé cette validation existante — les deux
    #: champs coexistent, chacun pour son propre chemin d'exécution
    #: (`launch_mission` via sandbox pour `assigned_agent`, `POST /missions/
    #: {id}/run` via `AgentRegistryService` pour `agent_id`).
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
            "agent_id": self.agent_id,
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
    #: Passe « Real Model Evaluation » — réponse attendue en texte libre,
    #: consultée UNIQUEMENT par `apex.oracles.response_judge.
    #: ExactMatchResponseJudge` (jamais par `ExitCodeOracle`, qui n'inspecte
    #: pas `task`). `NULL` par défaut : une tâche déterministe existante n'a
    #: jamais ce champ renseigné, jamais rétrofitté.
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Passe « Generated-Solution Evaluation Loop » (N+19) — template de
    #: script portant le marqueur de substitution du code candidat + les
    #: tests cachés, consulté UNIQUEMENT par `EvalRunnerService.
    #: run_generated_solution` (jamais par `run()`/`run_model_eval()`).
    #: `NULL` par défaut — aucune tâche existante n'a besoin de ce champ.
    solution_harness: Mapped[str | None] = mapped_column(Text, nullable=True)
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
            "expected_answer": self.expected_answer,
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

    Passe « Real Model Evaluation » : `clone_id`/`sandbox_status` sont
    devenus NULLABLE — un `EvalRun` produit par
    `EvalRunnerService.run_model_eval` (chemin `ModelProvider`, JAMAIS de
    sandbox) n'a structurellement ni Clone exécutant ni statut de processus
    à rapporter ; leur forcer une valeur aurait exigé soit un Clone/statut
    fabriqué (deviné), soit détourner le vocabulaire `ExecutionStatus`
    (dont chaque valeur exige une preuve noyau distincte, voir
    `apex.sandbox.model`) pour désigner un appel HTTP — les deux rejetés.
    Une ligne `eval_runs` du chemin déterministe existant (`run`) continue
    de les renseigner exactement comme avant, aucun backfill nécessaire.
    `model_id` (nouveau, nullable) est le nom RÉELLEMENT renvoyé par le
    backend (`GenerationResult.model_used`), jamais le nom demandé —
    même discipline que `AgentExecutionRow.model_id` : `NULL` tant
    qu'aucune génération n'a réellement abouti.
    """

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    clone_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dataset_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Étiquette libre de regroupement — voir docstring de la classe.
    experiment_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    oracle_verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    oracle_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Passe « Real Model Evaluation » — voir docstring de la classe.
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
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
            "model_id": self.model_id,
            "facts": dict(self.facts),
            "created_by": self.created_by,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat(),
        }


class ExperienceRow(Base):
    """Persistance d'une `apex.metalearner.experience.Experience` (boucle
    d'apprentissage adaptative, N+20) — **INSERT seul**, même garantie que
    `TaskRow`/`EvalRunRow` : une expérience, réussie ou non, n'est jamais
    modifiée ni supprimée après coup (une correction est une nouvelle ligne
    de génération suivante, jamais un UPDATE de celle-ci).

    Distincte d'`EvalRunRow` : un `EvalRun` est le résultat BRUT d'UNE
    tentative (jugement + statut d'exécution) ; une `Experience` est
    l'INTERPRÉTATION de ce résultat pour la boucle d'apprentissage — fitness
    réel (`apex.metalearner.population_fitness.compute_fitness`, réutilisé,
    jamais recalculé ici), catégorie d'échec réelle
    (`apex.metalearner.failure_taxonomy.classify_failure`, réutilisée), et
    surtout la GÉNÉALOGIE (`generation_number`/`parent_experience_id`/
    `mutation_description`) qui n'a structurellement aucune place sur
    `EvalRunRow` (une tâche d'évaluation Phase 5 ne connaît rien de la
    notion de génération évolutive). `eval_run_id` référence l'`EvalRun`
    RÉELLEMENT produit — pas de `ForeignKey` (même simplification documentée
    que `MissionRow.assigned_agent`), vérifiée au niveau service.

    `parent_experience_id`/`parent_generation` restent `NULL` pour une
    expérience de génération 0 (aucun parent, premier essai) — jamais une
    valeur inventée pour satisfaire une contrainte de forme.
    `solution_fingerprint` est un hash SHA-256 du CODE CANDIDAT réellement généré (jamais le texte
    brut, potentiellement long — voir `apex.evalrunner.service.
    _MAX_STORED_RESPONSE_CHARS` pour la même discipline de non-stockage
    illimité), permettant de détecter si deux tentatives ont produit un code
    identique sans dupliquer ce code une seconde fois."""

    __tablename__ = "experiences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    eval_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    generation_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    solution_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oracle_verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    fitness: Mapped[float] = mapped_column(Float, nullable=False)
    failure_category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    parent_experience_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mutation_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvement_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    experiment_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    #: `apex.metalearner.experience.SuccessEvidence` ; NULL sur les lignes antérieures à la colonne.
    success_evidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "eval_run_id": self.eval_run_id,
            "candidate_id": self.candidate_id,
            "model_id": self.model_id,
            "strategy_id": self.strategy_id,
            "generation_number": self.generation_number,
            "generation_config": dict(self.generation_config),
            "solution_fingerprint": self.solution_fingerprint,
            "execution_status": self.execution_status,
            "exit_code": self.exit_code,
            "oracle_verdict": self.oracle_verdict,
            "fitness": self.fitness,
            "failure_category": self.failure_category,
            "parent_experience_id": self.parent_experience_id,
            "parent_generation": self.parent_generation,
            "mutation_description": self.mutation_description,
            "improvement_delta": self.improvement_delta,
            "experiment_ref": self.experiment_ref,
            "success_evidence": self.success_evidence,
            "created_by": self.created_by,
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


class PopulationMemberRow(Base):
    """Persistance d'un `apex.metalearner.population_model.PopulationMember` (passe
    « Real Clone Population + Evolution Loop »).

    Une « population » N'EST PAS une table séparée : c'est simplement
    l'ensemble des lignes qui partagent `specialty` — exactement comme une
    `Population` de la mission N+6 est identifiée par une spécialité, pas
    par un identifiant arbitraire supplémentaire (aucun sur-engineering :
    zéro nouvelle table pour un concept qui se déduit entièrement d'une
    colonne déjà nécessaire).

    Réutilise `agents.clone_id` et `genome_versions.id` PAR VALEUR (pas de
    `ForeignKey` déclarée — même choix documenté qu'`AgentRow.pool_id` : ces
    tables ne sont pas encore synchronisées transactionnellement entre
    elles) : un membre de population EST un `Clone` réel exécuté avec UNE
    `GenomeVersion` réelle, jamais une troisième représentation dupliquée de
    l'un ou l'autre.

    `parent_member_id` porte la généalogie AU NIVEAU POPULATION (quel membre
    a produit celui-ci par mutation/fork) — distincte de
    `genome_versions.parent_version_id` (généalogie du CONTENU du Genome) :
    les deux coexistent car un même Genome pourrait en théorie être
    réutilisé par plusieurs membres, même si cette passe n'exploite pas
    encore ce cas.

    `experiment_ref` est la clé d'IDEMPOTENCE (mandat §17 de la mission) :
    `PopulationMemberRepository.get_by_experiment_ref` est vérifié AVANT
    toute création — rejouer la même expérimentation ne crée jamais un
    second membre, exactement le patron déjà établi par
    `apex.metalearner.feedback.LearningFeedbackService` (`EventRepository`
    y jouait ce rôle faute de table dédiée ; ici une table dédiée existe
    déjà, donc l'idempotence est vérifiée directement dessus, pas
    réinventée via un second mécanisme).

    `status` est la SEULE colonne mutable en place (`experimental` ->
    `elite`/`champion`/`hibernating`/`retired`, `retired` terminal) — même
    discipline que `SkillRow`/`GenomeVersionRow` : jamais réutilisée pour
    autre chose que ce changement de statut, l'historique (fitness,
    provenance) reste immuable une fois écrit.
    """

    __tablename__ = "population_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    clone_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    genome_version_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    fitness: Mapped[float | None] = mapped_column(Float, nullable=True)
    fitness_components: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    skill_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    genome_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    experiment_ref: Mapped[str] = mapped_column(String(160), nullable=False, index=True, unique=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "specialty": self.specialty,
            "generation": self.generation,
            "clone_id": self.clone_id,
            "genome_version_id": self.genome_version_id,
            "parent_member_id": self.parent_member_id,
            "status": self.status,
            "fitness": self.fitness,
            "fitness_components": dict(self.fitness_components) if self.fitness_components else None,
            "skill_candidate_id": self.skill_candidate_id,
            "genome_candidate_id": self.genome_candidate_id,
            "experiment_ref": self.experiment_ref,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }


class LearningSessionRow(Base):
    """Persiste `apex.metalearner.learning_session_model.LearningSession`
    (passe « Autonomous Multi-Generation Learning Orchestrator ») — une
    boucle RÉPÉTÉE de `PopulationEvolutionService.evolve_generation`,
    jamais une seconde implémentation de la boucle évolutive elle-même.

    **Exclusion mutuelle RÉELLEMENT inter-processus** — l'index UNIQUE
    partiel `learning_sessions_one_running_per_specialty_idx` (`specialty`
    WHERE `status = 'running'`, migration) est le SEUL mécanisme qui
    empêche deux sessions `running` simultanées pour la même spécialité :
    contrairement à `CycleAlreadyRunning`
    (`apex.metalearner.autonomy.AutonomousLearningService`, verrou mémoire
    documenté comme valide PAR PROCESSUS seulement), cette garantie tient
    même avec plusieurs workers API — le SGBD est le seul arbitre, jamais
    un verrou applicatif (même philosophie que `MissionRepository.
    claim_running`, `apex/db/repositories.py`).

    `best_member_id`/`best_fitness`/`best_genome_version_id` REFLÈTENT
    simplement le `CHAMPION` courant de la population de cette spécialité
    (`apex.metalearner.population_service.PopulationService.get_champion`)
    après chaque génération — jamais un second calcul de « meilleur » : le
    Champion porte déjà l'invariant « jamais remplacé par un individu
    moins performant » (passe N+6), la session ne fait que le recopier
    pour un accès direct sans recalcul.

    `generations_completed` et le numéro de génération réellement produit
    par `evolve_generation` (dérivé de `population_members.generation`,
    jamais fourni par cette session) restent deux sources de vérité
    délibérément séparées mais toujours cohérentes : reprendre une session
    n'a JAMAIS besoin de rejouer un numéro de génération — `evolve_
    generation` le dérive lui-même de l'état de population déjà persisté,
    ce qui rend la reprise sûre par construction (aucun doublon de
    génération/membre/lignage possible, voir la docstring de
    `LearningOrchestrator`).

    `status` : `running` -> `completed`/`failed`/`cancelled` (états
    terminaux) ou `cancel_requested` (transitoire, vérifié entre deux
    générations par la boucle elle-même, jamais une interruption en cours
    de génération — voir `LearningOrchestrator._run_loop`)."""

    __tablename__ = "learning_sessions"
    __table_args__ = (
        # Exclusion mutuelle RÉELLE inter-processus (voir docstring de la
        # classe) — déclarée ici, PAS seulement dans la migration, pour
        # que `Base.metadata.create_all()` (SQLite en mémoire, tests
        # unitaires) l'applique aussi ; la migration Alembic répète le même
        # DDL par cohérence avec le reste de ce module (index créés via
        # `op.execute`, jamais l'autogénération).
        Index(
            "learning_sessions_one_running_per_specialty_idx", "specialty", unique=True,
            postgresql_where=text("status = 'running'"), sqlite_where=text("status = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    max_generations: Mapped[int] = mapped_column(Integer, nullable=False)
    generations_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    convergence_patience: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generations_since_improvement: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    best_fitness: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_genome_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "specialty": self.specialty,
            "status": self.status,
            "max_generations": self.max_generations,
            "generations_completed": self.generations_completed,
            "convergence_patience": self.convergence_patience,
            "generations_since_improvement": self.generations_since_improvement,
            "best_member_id": self.best_member_id,
            "best_fitness": self.best_fitness,
            "best_genome_version_id": self.best_genome_version_id,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class GauntletResultRow(Base):
    """Persiste `apex.metalearner.judge_model.GauntletResult` (passe
    « Strict Judge System + Gauntlet Learning Loop ») — la décision AGRÉGÉE,
    déterministe, d'un ou plusieurs juges spécialisés sur UN membre de
    population, à UN stage donné (`cheap` : par membre, sans exécution
    supplémentaire ; `full` : au moment de la candidature CHAMPION,
    inclut les juges qui exigent une exécution réelle supplémentaire —
    voir `apex.metalearner.gauntlet.GauntletRunner`).

    `member_id` référence `population_members.id` PAR VALEUR (même choix
    documenté qu'`PopulationMemberRow.clone_id`/`genome_version_id` : ces
    tables ne sont pas synchronisées transactionnellement entre elles) —
    UN membre peut porter PLUSIEURS lignes (`cheap` puis, seulement s'il
    devient candidat champion, `full`), jamais une mise à jour en place :
    chaque évaluation reste un fait immuable, exactement la discipline déjà
    appliquée à `population_members.fitness`/`eval_runs`.

    `judge_results` (JSON) porte la liste STRUCTURÉE des jugements
    individuels (`JudgeResult.to_dict()`, un par juge exécuté) — jamais
    seulement un texte narratif (§10 de la mission : une décision doit être
    traçable jusqu'à ses preuves). `protocol_version` (§17) rend un
    jugement déjà persisté interprétable même après une future évolution
    de `apex.metalearner.gauntlet._aggregate` — jamais invalidé
    rétroactivement par un changement de règle."""

    __tablename__ = "gauntlet_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    protocol_version: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    veto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    veto_dimension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    disagreement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stopped_after_critical_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    judge_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    compared_champion_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "specialty": self.specialty,
            "generation": self.generation,
            "member_id": self.member_id,
            "stage": self.stage,
            "protocol_version": self.protocol_version,
            "decision": self.decision,
            "overall_score": self.overall_score,
            "overall_confidence": self.overall_confidence,
            "veto": self.veto,
            "veto_dimension": self.veto_dimension,
            "disagreement": self.disagreement,
            "stopped_after_critical_failure": self.stopped_after_critical_failure,
            "judge_results": self.judge_results,
            "compared_champion_id": self.compared_champion_id,
            "created_at": self.created_at.isoformat(),
        }


class JudgeLearningSignalRow(Base):
    """Persiste `apex.metalearner.judge_feedback_model.JudgeLearningSignal`
    (passe « Judge Feedback -> Weakness -> Curriculum -> Adaptive
    Evolution », N+9) — le feedback structuré d'UN `JudgeResult`, dans le
    contexte d'UN `GauntletResult` (`gauntlet_results`, N+8).

    INSERT seul (même garantie que `gauntlet_results`/`eval_runs`) : un
    signal, une fois produit, n'est jamais modifié — l'historique complet
    (§10 de la mission : « Learning Memory ») reste la SEULE source de
    vérité, jamais un agrégat mutable séparé (`apex.metalearner.
    weakness_engine.GauntletWeakness` est TOUJOURS recalculé à la demande
    depuis ces lignes, même discipline qu'`apex.weakness.model.
    WeaknessReport` vis-à-vis d'`eval_runs`).

    `genome_version_id` référencé PAR VALEUR (même choix qu'ailleurs dans ce
    module) — permet la corrélation par lignage (§5) sans jointure
    déclarée. `category`/`failure_category` distinguent explicitement une
    faiblesse RÉELLE du candidat d'une panne d'évaluation/infrastructure
    (§11 — jamais confondues)."""

    __tablename__ = "judge_learning_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    gauntlet_result_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    genome_version_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    judge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    judge_version: Mapped[str] = mapped_column(String(16), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    finding_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(36), nullable=True)
    protocol_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "gauntlet_result_id": self.gauntlet_result_id,
            "member_id": self.member_id,
            "specialty": self.specialty,
            "generation": self.generation,
            "genome_version_id": self.genome_version_id,
            "judge_id": self.judge_id,
            "judge_version": self.judge_version,
            "dimension": self.dimension,
            "decision": self.decision,
            "category": self.category,
            "failure_category": self.failure_category,
            "severity": self.severity,
            "score": self.score,
            "confidence": self.confidence,
            "finding_summary": self.finding_summary,
            "evidence_reference": self.evidence_reference,
            "protocol_version": self.protocol_version,
            "created_at": self.created_at.isoformat(),
        }


class CurriculumChallengeRow(Base):
    """Persiste `apex.metalearner.challenge_model.CurriculumChallenge`
    (passe « Adversarial Curriculum + Capability Benchmarking », N+10) —
    référence une `apex.tasks.model.Task` RÉELLEMENT créée (`task_id`),
    jamais un résultat attendu fabriqué (§2 de la mission).

    INSERT seul — même garantie que `judge_learning_signals` : un challenge
    produit n'est jamais modifié ni supprimé. `weakness_id` est une clé
    DÉRIVÉE (`apex.metalearner.challenge_model.weakness_key`), pas une clé
    étrangère vers une table de faiblesses (`GauntletWeakness` reste
    calculée à la demande, N+9)."""

    __tablename__ = "curriculum_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    weakness_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    evaluation_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    heldout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adversarial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    protocol_version: Mapped[str] = mapped_column(String(16), nullable=False)
    #: N+11 (« Universal Task + Curriculum Engine », § Phase 16/23) —
    #: colonnes ADDITIVES nullables, extension de cette même table plutôt
    #: qu'une nouvelle (§23 : « réutiliser les tables existantes quand elles
    #: suffisent »). `parent_task_id` référence la tâche transformée par
    #: `apex.taskgen.variant_engine.VariantEngine` (`None` pour un challenge
    #: N+10 sans parent). `seed` : la graine déterministe utilisée par la
    #: sélection de variante (`None` si aucune, comportement N+10 inchangé).
    parent_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "specialty": self.specialty,
            "weakness_id": self.weakness_id,
            "dimension": self.dimension,
            "difficulty": self.difficulty,
            "generation": self.generation,
            "task_id": self.task_id,
            "evaluation_criteria": self.evaluation_criteria,
            "source": self.source,
            "heldout": self.heldout,
            "adversarial": self.adversarial,
            "protocol_version": self.protocol_version,
            "parent_task_id": self.parent_task_id,
            "seed": self.seed,
            "created_at": self.created_at.isoformat(),
        }


class BugRecordRow(Base):
    """Persiste `apex.metalearner.bug_farm_model.BugRecord` (passe
    « Adversarial Curriculum + Capability Benchmarking », N+10, §7) — un
    bug RÉELLEMENT observé (dérivé de `judge_learning_signals`, N+9), avec
    un statut MUTABLE (`open`/`fixed`/`regressed`) : la SEULE table N+10
    dont une ligne change après sa création (même discipline que
    `population_members.status` — des colonnes ciblées, jamais une ligne
    entière réécrite, voir `apex.db.repositories.BugRecordRepository`)."""

    __tablename__ = "bug_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    weakness_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, unique=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    member_id: Mapped[str] = mapped_column(String(36), nullable=False)
    genome_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    fixed_at_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regressed_at_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "specialty": self.specialty,
            "weakness_id": self.weakness_id,
            "dimension": self.dimension,
            "failure_category": self.failure_category,
            "severity": self.severity,
            "first_seen_generation": self.first_seen_generation,
            "first_seen_at": self.first_seen_at.isoformat(),
            "member_id": self.member_id,
            "genome_version_id": self.genome_version_id,
            "evidence_reference": self.evidence_reference,
            "status": self.status,
            "fixed_at_generation": self.fixed_at_generation,
            "regressed_at_generation": self.regressed_at_generation,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ProjectRow(Base):
    """Persiste `apex.projects.model.Project` (passe « Première interface
    utilisable ») — structure MINIMALE : un projet n'est qu'un regroupement
    nommé de conversations, sans jalons/kanban/timeline (délibérément hors
    scope, voir `ProjectManagerPage`, resté `ui-scaffold` pour ces aspects)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


class ConversationRow(Base):
    """Persiste `apex.conversations.model.Conversation` — un fil de Chat,
    distinct d'une `MissionRow` : un tour de conversation n'est jamais
    soumis à la gouvernance REVIEW (ADR-0011), voir `apex.conversations.
    service.ConversationService.send_message`. Pas de `ForeignKey` vers
    `projects.id` : même simplification documentée que `MissionRow.
    assigned_agent` — l'existence est vérifiée au niveau service, pas ici."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "project_id": self.project_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class MessageRow(Base):
    """Un tour de `ConversationRow`. `model_id`/`provider_id` restent `NULL`
    pour un message `role="user"` (jamais générés) ; pour `role="assistant"`,
    `NULL` signifie « aucun candidat n'a pu être résolu avant même une
    tentative de génération » (`status="error"`, voir `ConversationService.
    send_message`) — distinct d'un `provider_id` renseigné dont l'appel a
    échoué (`status="error"` également, mais `content` porte alors le détail
    de l'échec, jamais un texte inventé à sa place)."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


class AgentTemplateRow(Base):
    """Persiste `apex.agent_templates.model.AgentTemplate` — une DÉFINITION
    d'agent (nom, rôle, modèle préféré, outils déclarés), distincte d'un
    `AgentRow` (une instance de clone RÉELLEMENT en cours d'exécution,
    `AgentRuntime`). Créer/lister/supprimer un template est réel (cette
    table) ; le FAIRE TOURNER (spawn réel depuis un template) reste
    `PLANNED` — `apex.interfaces.registry` le documente explicitement,
    jamais présenté comme exécutable ici. `tools` reste une liste déclarée,
    non appliquée : `ToolProvider`/`ToolRegistry` sont eux-mêmes `PLANNED`
    (voir le registre), aucune vérification d'usage réel n'existe encore."""

    __tablename__ = "agent_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    model_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "model_role": self.model_role,
            "tools": list(self.tools or []),
            "description": self.description,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }


class ConfiguredAgentRow(Base):
    """Persiste `apex.agent_registry.model.Agent` — passe « Agents V1 ».
    Nommée `configured_agents` (jamais `agents`, déjà pris par `AgentRow` —
    l'instance de clone OS-level réelle, `AgentRuntime`) : cette table décrit
    une CONFIGURATION réutilisable (modèle préféré, outils/permissions
    déclarés, instructions), jamais un processus en cours d'exécution.
    `required_capabilities`/`tools`/`permissions` sont des listes JSON de
    valeurs de vocabulaires FERMÉS (`apex.routing.model.Capability`, `apex.
    agent_registry.model.ToolName`/`Permission`), validées au niveau service,
    pas ici — même discipline que `MissionRow.specialty`."""

    __tablename__ = "configured_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    specialty: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Désactivation explicite — un agent `enabled=False` n'est jamais
    #: exécutable, vérifié par `AgentRegistryService.run_execution` avant
    #: toute tentative (jamais une simple convention UI).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "specialty": self.specialty,
            "model_role": self.model_role,
            "required_capabilities": list(self.required_capabilities or []),
            "tools": list(self.tools or []),
            "permissions": list(self.permissions or []),
            "instructions": self.instructions,
            "enabled": self.enabled,
            "project_id": self.project_id,
            "template_id": self.template_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class AgentExecutionRow(Base):
    """Persiste `apex.agent_registry.model.AgentExecution` — INSERT + un
    seul `UPDATE` ciblé (`AgentExecutionRepository.complete`, champs de
    complétion uniquement), même discipline que `SkillRow.decide`. `model_id`/
    `provider_id`/`result` restent `NULL` tant qu'aucun candidat n'a
    réellement été servi — jamais une valeur devinée. `error` porte
    TOUJOURS le détail complet des tentatives (`generate_with_fallback`),
    jamais seulement la dernière."""

    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    mission_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: Passe « Adaptive Model Router V1 » — mêmes noms que
    #: `apex.models.model.GenerationResult` (`elapsed_s`/`tokens_prompt`/
    #: `tokens_completion`), pour une traçabilité directe entre la mesure
    #: réelle produite par `ModelProvider.generate()` et sa persistance ici.
    #: `NULL` tant qu'aucune génération n'a réellement abouti (échec total,
    #: candidat jamais tenté) OU que le backend n'a pas fourni le comptage
    #: de tokens (`Measurement.status is not REAL`) — jamais une valeur
    #: `0` ou devinée à la place d'une mesure absente.
    elapsed_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_completion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "mission_id": self.mission_id,
            "status": self.status,
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "result": self.result,
            "error": self.error,
            "fallback_reason": self.fallback_reason,
            "elapsed_s": self.elapsed_s,
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class AgentTeamRow(Base):
    """Persiste `apex.agent_registry.model.AgentTeam` — structure V1
    délibérément minimale (directive « Agents V1 » §13) : nom, description,
    membres. Aucune communication autonome, aucun ordre d'exécution imposé
    ici — un futur workflow multi-agent réel étendrait cette table plutôt
    que d'en créer une seconde."""

    __tablename__ = "agent_teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    agent_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_ids": list(self.agent_ids or []),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }
