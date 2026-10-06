"""task_runs.tool_permissions / max_tool_steps — mode outillé du TaskLoop

Revision ID: f2a4c6e8b0d1
Revises: d8f1a3c5e7b9
Create Date: 2026-10-06

`tool_permissions` : permissions EXPLICITEMENT accordées aux outils pour cette TaskRun (liste JSON, NULL : mode
sans outils). `max_tool_steps` : tours maximaux d'un épisode outillé par tentative. Colonnes nullables : aucune
ligne existante n'est réécrite. Permissions de la table inchangées (c8e2f4a6b9d1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a4c6e8b0d1"
down_revision: str | None = "d8f1a3c5e7b9"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("tool_permissions", sa.JSON(), nullable=True))
    op.add_column("task_runs", sa.Column("max_tool_steps", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "max_tool_steps")
    op.drop_column("task_runs", "tool_permissions")
