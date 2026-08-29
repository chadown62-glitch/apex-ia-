"""Modèles SQLAlchemy — schéma déclaratif, indépendant du moteur.

Portée exacte du brief phase 2 (`docs/VISION_INTEGRATION.md` §5) :

- `ComputePool` -> table `compute_pools`, avec `trust_level` (principe 6).
- `EventSink` (stub -> implémenté) -> table `events`.
- `audit_log` -> écriture seule pour les rôles applicatifs ; la révocation
  `UPDATE`/`DELETE`/`TRUNCATE` est appliquée en migration (PostgreSQL), pas
  ici. Ce module ne fait que décrire la forme des données.

Ce que ce module ne fait PAS :
- il ne crée aucune table pour des abstractions encore `PLANNED`
  (`task_runs`, `model_runs`, `tool_usage`, `sandbox_execs` — TaskBank,
  ModelProvider, ToolProvider, SandboxProvider n'existent pas avant les
  phases 3 à 7). Créer leur schéma maintenant reviendrait à deviner une
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

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
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
