"""task_runs.strategy / base_temperature — protocole v2 (A/B/C)

Revision ID: b5d7f9a1c3e2
Revises: e4b6d8f0a2c3
Create Date: 2026-10-05

`strategy` : ce qui est tiré des Experience de `condition_on_run` (`rule` = règle existante, bras B ;
`failure_facts` = faits d'échec autorisés, bras C). `base_temperature` : température explicite quand aucune
règle ne la fixe (RUN 1, bras A et C). Colonnes nullables : aucune ligne existante n'est réécrite (NULL =
comportement historique). Permissions de la table inchangées (c8e2f4a6b9d1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5d7f9a1c3e2"
down_revision: str | None = "e4b6d8f0a2c3"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("strategy", sa.String(32), nullable=True))
    op.add_column("task_runs", sa.Column("base_temperature", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "base_temperature")
    op.drop_column("task_runs", "strategy")
