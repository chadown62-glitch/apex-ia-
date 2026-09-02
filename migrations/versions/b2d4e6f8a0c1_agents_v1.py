"""configured_agents/agent_executions/agent_teams + missions.agent_id —
passe « Agents V1 »

Revision ID: b2d4e6f8a0c1
Revises: a1c2d3e4f5b6
Create Date: 2026-09-02

Trois tables neuves :
- `configured_agents` : `apex.agent_registry.model.Agent` — une entité
  réutilisable, réellement assignable à des Missions, distincte d'`agents`
  (déjà pris — `AgentRow`, un clone OS-level réel, `AgentRuntime`) et
  d'`agent_templates` (un blueprint, jamais exécutable).
- `agent_executions` : `apex.agent_registry.model.AgentExecution` — trace
  réelle d'une tentative d'exécution (candidat servi si connu, jamais
  deviné, `NULL` sinon).
- `agent_teams` : structure V1 minimale (§13 de la directive).

Une colonne ajoutée : `missions.agent_id`, DISTINCTE d'`assigned_agent`
(clone) — voir `apex.db.models.MissionRow.agent_id` pour le motif complet.

Permissions, même motif que `conversations`/`projects`/`agent_templates`
(migration `a1c2d3e4f5b6`) : `apex_core` reçoit `SELECT, INSERT, UPDATE,
DELETE` sur les trois tables neuves (DELETE inclus — un `Agent`/une `Team`
n'a pas de généalogie de révision à préserver, une suppression est une
action humaine explicite) ; `apex_readonly` reçoit `SELECT` seul ;
`apex_agent`/`apex_eval`/`apex_audit` n'ont aucun accès (un clone n'écrit
jamais sa propre configuration d'Agent, même motif que `missions`/`skills`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4e6f8a0c1"
down_revision: str | None = "a1c2d3e4f5b6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLES = ("configured_agents", "agent_executions", "agent_teams")


def upgrade() -> None:
    op.create_table(
        "configured_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("specialty", sa.String(64), nullable=True),
        sa.Column("model_role", sa.String(64), nullable=True),
        sa.Column("required_capabilities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tools", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("template_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX configured_agents_role_idx ON configured_agents (role)")
    op.execute("CREATE INDEX configured_agents_project_id_idx ON configured_agents (project_id)")
    op.execute("CREATE INDEX configured_agents_created_at_idx ON configured_agents (created_at)")

    op.create_table(
        "agent_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("provider_id", sa.String(200), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fallback_reason", sa.String(24), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE INDEX agent_executions_agent_id_idx ON agent_executions (agent_id)")
    op.execute("CREATE INDEX agent_executions_mission_id_idx ON agent_executions (mission_id)")
    op.execute("CREATE INDEX agent_executions_status_idx ON agent_executions (status)")
    op.execute("CREATE INDEX agent_executions_started_at_idx ON agent_executions (started_at)")

    op.create_table(
        "agent_teams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("agent_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX agent_teams_created_at_idx ON agent_teams (created_at)")

    op.add_column("missions", sa.Column("agent_id", sa.String(36), nullable=True))
    op.execute("CREATE INDEX missions_agent_id_idx ON missions (agent_id)")

    for table in _TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO apex_core")
        op.execute(f"GRANT SELECT ON {table} TO apex_readonly")
        op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table} FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS missions_agent_id_idx")
    op.drop_column("missions", "agent_id")
    op.execute("DROP TABLE IF EXISTS agent_teams")
    op.execute("DROP TABLE IF EXISTS agent_executions")
    op.execute("DROP TABLE IF EXISTS configured_agents")
