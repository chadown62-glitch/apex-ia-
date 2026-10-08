"""task_runs.plan_policy — politique de l'étape PLAN (`request` | `auto`, docs/VALIDATION_REELLE.md §6.4)

Revision ID: f9b1d3e5a7c9
Revises: e8a0c2d4f6b8
Create Date: 2026-10-08

NULL : TaskRun antérieure à ce champ (aucune ligne réécrite ; relue comme `request`). Permissions inchangées.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9b1d3e5a7c9"
down_revision: str | None = "e8a0c2d4f6b8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("plan_policy", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "plan_policy")
