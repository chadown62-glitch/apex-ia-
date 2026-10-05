"""task_runs.retry_adaptation / plan_first — boucle de tâche adaptative

Revision ID: d8f1a3c5e7b9
Revises: b5d7f9a1c3e2
Create Date: 2026-10-05

`retry_adaptation` : ce que la boucle change entre deux tentatives d'une même TaskRun (`failure_facts` : la
tentative suivante reçoit les faits d'échec autorisés de la précédente ; `none` : même prompt). `plan_first` :
l'étape PLAN a demandé une analyse au modèle avant la génération. Colonnes nullables : aucune ligne existante
n'est réécrite (NULL = TaskRun antérieure à ces champs). Permissions de la table inchangées (c8e2f4a6b9d1).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f1a3c5e7b9"
down_revision: str | None = "b5d7f9a1c3e2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("retry_adaptation", sa.String(32), nullable=True))
    op.add_column("task_runs", sa.Column("plan_first", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_runs", "plan_first")
    op.drop_column("task_runs", "retry_adaptation")
