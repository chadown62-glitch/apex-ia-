"""task_runs.context/constraints/resume_count — reprise réelle d'une TaskRun interrompue

Revision ID: e8a0c2d4f6b8
Revises: d5f7a9c1e3b5
Create Date: 2026-10-06

La reprise reconstruit la requête d'origine (dont les textes additifs `context`/`constraints`) et compte les
reprises. NULL pour les lignes existantes (aucune réécrite). Permissions de la table inchangées.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a0c2d4f6b8"
down_revision: str | None = "d5f7a9c1e3b5"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("context", sa.Text(), nullable=True))
    op.add_column("task_runs", sa.Column("constraints", sa.Text(), nullable=True))
    op.add_column("task_runs", sa.Column("resume_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "resume_count")
    op.drop_column("task_runs", "constraints")
    op.drop_column("task_runs", "context")
