"""agents table — persistance AgentRuntime (registre : PLANNED -> STUB)

Revision ID: 199166dcfdeb
Revises: a6227d98f7d7
Create Date: 2026-08-29

docs/COGNITIVE_ARCHITECTURE.md §9 nommait déjà cette table comme future,
créée seulement "quand AgentRuntime existe" (ADR-0007) — c'est le cas
maintenant. Pas de partitionnement : `agents` n'appartient pas aux cinq
tables à forte cardinalité identifiées par docs/PHASE_1_REPORT.md §6.

Permissions, motif identique à la migration initiale (ADR-0010) : seuls
`apex_core` (qui spawn/termine réellement les clones, PolicyEngine ne
l'autorise qu'à CORE/HUMAN) et `apex_readonly` touchent cette table. Aucun
rôle de clone (`apex_agent`, `apex_eval`, `apex_audit`) n'y a accès : un
clone n'écrit jamais sa propre ligne dans `agents`, seul le runtime le fait
pour son compte — cohérent avec l'allow-list de PolicyEngine.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "199166dcfdeb"
down_revision: str | None = "a6227d98f7d7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("clone_id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("mission_id", sa.String(64), nullable=True),
        sa.Column("genome_ref", sa.String(128), nullable=False),
        sa.Column("spawned_by", sa.String(16), nullable=False),
        sa.Column("pool_id", sa.String(64), nullable=False),
        sa.Column("isolation_level", sa.String(32), nullable=False),
        sa.Column("retries_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE INDEX agents_state_idx ON agents (state)")
    op.execute("CREATE INDEX agents_created_at_idx ON agents (created_at)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON agents TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON agents FROM apex_core")
    op.execute("GRANT SELECT ON agents TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON agents FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agents")
