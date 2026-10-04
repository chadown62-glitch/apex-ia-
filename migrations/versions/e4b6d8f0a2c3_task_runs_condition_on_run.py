"""task_runs.condition_on_run — TaskRun conditionnée par les Experience d'une TaskRun précédente

Revision ID: e4b6d8f0a2c3
Revises: c8e2f4a6b9d1
Create Date: 2026-10-04

Chantier « RUN 1 -> Experience -> RUN 2 » : une TaskRun peut demander que la stratégie de génération soit
dérivée des `Experience` d'une TaskRun TERMINÉE de la même tâche (règle existante
`apex.metalearner.adaptive_solution_loop.decide_strategy_from_experiences`). NULL = bras « baseline »,
aucune expérience lue. Colonne nullable : aucune ligne existante n'est réécrite. Les permissions de la
table (c8e2f4a6b9d1) s'appliquent telles quelles.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b6d8f0a2c3"
down_revision: str | None = "c8e2f4a6b9d1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("condition_on_run", sa.String(36), nullable=True))
    op.create_index("ix_task_runs_condition_on_run", "task_runs", ["condition_on_run"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_condition_on_run", table_name="task_runs")
    op.drop_column("task_runs", "condition_on_run")
