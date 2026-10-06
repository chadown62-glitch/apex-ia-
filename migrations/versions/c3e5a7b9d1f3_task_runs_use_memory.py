"""task_runs.use_memory — la TaskRun a-t-elle demandé la récupération en mémoire système

Revision ID: c3e5a7b9d1f3
Revises: b9d1f3a5c7e9
Create Date: 2026-10-06

NULL : TaskRun antérieure à ce champ (aucune ligne réécrite). Permissions de la table inchangées.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f3"
down_revision: str | None = "b9d1f3a5c7e9"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("use_memory", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "use_memory")
