"""Assistant APEX 0.4.0 : consignes de projet, mode des conversations, méta des messages, tours, pièces jointes,
paramètres de l'application

Revision ID: c2e4a6b8d0f1
Revises: f9b1d3e5a7c9
Create Date: 2026-10-09

Colonnes ajoutées nullables : aucune ligne existante n'est réécrite (projet sans consignes, conversation relue
comme mode `normal`, message sans méta). Trois tables nouvelles, sans clé étrangère (même simplification
documentée que `conversations.project_id`) : l'existence est vérifiée au niveau service.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e4a6b8d0f1"
down_revision: str | None = "f9b1d3e5a7c9"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("instructions", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("mode", sa.String(16), nullable=True))
    op.add_column("messages", sa.Column("meta", sa.JSON(), nullable=True))
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("user_message_id", sa.String(36), nullable=False),
        sa.Column("assistant_message_id", sa.String(36), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_conversation_turns_conversation_id", "conversation_turns", ["conversation_id"])
    op.create_index("ix_conversation_turns_status", "conversation_turns", ["status"])
    op.create_table(
        "project_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_attachments_project_id", "project_attachments", ["project_id"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Mêmes droits que les tables de conversations (a1c2d3e4f5b6) : le rôle applicatif lit et écrit, le rôle
    # en lecture seule lit. PostgreSQL seulement (la base embarquée du profil autonome n'a pas de rôles).
    if op.get_bind().dialect.name == "postgresql":
        for table in ("conversation_turns", "project_attachments", "app_settings"):
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO apex_core")
            op.execute(f"GRANT SELECT ON {table} TO apex_readonly")


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_project_attachments_project_id", "project_attachments")
    op.drop_table("project_attachments")
    op.drop_index("ix_conversation_turns_status", "conversation_turns")
    op.drop_index("ix_conversation_turns_conversation_id", "conversation_turns")
    op.drop_table("conversation_turns")
    op.drop_column("messages", "meta")
    op.drop_column("conversations", "mode")
    op.drop_column("projects", "updated_at")
    op.drop_column("projects", "instructions")
