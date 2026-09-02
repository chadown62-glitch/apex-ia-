"""conversations/messages/projects/agent_templates — passe « Première
interface utilisable »

Revision ID: a1c2d3e4f5b6
Revises: 749e6b764126
Create Date: 2026-09-01

Quatre tables neuves, aucune existante modifiée :
- `projects` : regroupement nommé de conversations, structure minimale
  (voir `apex.projects.model.Project`).
- `conversations` / `messages` : Chat réel — un message `assistant` est
  généré par `apex.routing.select_model`/`generate_with_fallback`, JAMAIS
  soumis à la gouvernance REVIEW de `MissionRow` (un tour de conversation
  n'est pas une Mission).
- `agent_templates` : DÉFINITIONS d'agent (nom/rôle/modèle/outils),
  distinctes des instances réelles de `agents` (`AgentRuntime`) — créer/
  lister/supprimer un template est réel ; le faire tourner reste `PLANNED`.

Permissions, même motif que `missions`/`skills` (voir ces migrations) :
- `apex_core` : `SELECT, INSERT, UPDATE, DELETE` sur les quatre tables —
  DELETE inclus ici (contrairement à `skills`/`memory_records`) parce
  qu'une conversation/un message/un template n'a pas de généalogie de
  révision à préserver : supprimer une conversation est une action HUMAINE
  explicite et définitive, pas une correction de contenu.
- `apex_readonly` : `SELECT` seul sur les quatre.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès — un clone n'écrit
  jamais dans le Chat humain ni ses propres templates (même motif que
  `missions`/`skills`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2d3e4f5b6"
down_revision: str | None = "749e6b764126"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLES = ("projects", "conversations", "messages", "agent_templates")


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX projects_created_at_idx ON projects (created_at)")

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX conversations_project_id_idx ON conversations (project_id)")
    op.execute("CREATE INDEX conversations_created_at_idx ON conversations (created_at)")
    op.execute("CREATE INDEX conversations_updated_at_idx ON conversations (updated_at)")

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("provider_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX messages_conversation_id_idx ON messages (conversation_id)")
    op.execute("CREATE INDEX messages_created_at_idx ON messages (created_at)")

    op.create_table(
        "agent_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("model_role", sa.String(64), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX agent_templates_role_idx ON agent_templates (role)")
    op.execute("CREATE INDEX agent_templates_created_at_idx ON agent_templates (created_at)")

    for table in _TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO apex_core")
        op.execute(f"GRANT SELECT ON {table} TO apex_readonly")
        op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table} FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_templates")
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversations")
    op.execute("DROP TABLE IF EXISTS projects")
