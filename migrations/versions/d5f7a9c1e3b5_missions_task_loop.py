"""missions.task_id/task_options/task_run_id + task_runs.mission_id — une Mission exécutée par le TaskLoop

Revision ID: d5f7a9c1e3b5
Revises: c3e5a7b9d1f3
Create Date: 2026-10-06

Unification Missions/TaskLoop : une mission peut porter une Task vérifiable (TaskBank) exécutée par le chemin
autonome canonique (`apex.taskloop`) ; la TaskRun garde la mission qui l'a lancée. NULL partout pour les lignes
existantes (aucune réécrite). Permissions des tables inchangées.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5f7a9c1e3b5"
down_revision: str | None = "c3e5a7b9d1f3"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("missions", sa.Column("task_id", sa.String(length=36), nullable=True))
    op.add_column("missions", sa.Column("task_options", sa.JSON(), nullable=True))
    op.add_column("missions", sa.Column("task_run_id", sa.String(length=36), nullable=True))
    op.create_index("ix_missions_task_id", "missions", ["task_id"])
    op.add_column("task_runs", sa.Column("mission_id", sa.String(length=36), nullable=True))
    op.create_index("ix_task_runs_mission_id", "task_runs", ["mission_id"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_mission_id", table_name="task_runs")
    op.drop_column("task_runs", "mission_id")
    op.drop_index("ix_missions_task_id", table_name="missions")
    op.drop_column("missions", "task_run_id")
    op.drop_column("missions", "task_options")
    op.drop_column("missions", "task_id")
