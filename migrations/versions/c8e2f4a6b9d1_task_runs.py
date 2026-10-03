"""task_runs — l'exécution persistée d'une tâche par le TaskLoop (première boucle d'exécution réelle)

Revision ID: c8e2f4a6b9d1
Revises: a3e5c7d9f1b2
Create Date: 2026-10-03

Le résultat d'ensemble d'une exécution de `run_task_loop` (statut final, raison d'arrêt, tentatives,
modèles essayés, sortie) n'existait qu'en mémoire du processus. `id` = `trace_id` de l'exécution
(identique à `eval_runs.experiment_ref` et au `trace_id` des événements). Une ligne `running` puis une
mise à jour vers un statut terminal ; `interrupted` si le processus a disparu entre-temps.

Permissions, même motif que `learning_sessions` (ligne de suivi mise à jour, jamais supprimée) :
- `apex_core` : `SELECT, INSERT, UPDATE` — `DELETE`/`TRUNCATE` révoqués ;
- `apex_readonly` : `SELECT` seul ;
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2f4a6b9d1"
down_revision: str | None = "a3e5c7d9f1b2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(16), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("providers_tried", sa.JSON(), nullable=False),
        sa.Column("eval_run_ids", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.JSON(), nullable=False),
        sa.Column("final_output", sa.Text(), nullable=True),
        sa.Column("failure", sa.Text(), nullable=True),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_runs_task_id", "task_runs", ["task_id"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_started_at", "task_runs", ["started_at"])

    op.execute("GRANT SELECT, INSERT, UPDATE ON task_runs TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON task_runs FROM apex_core")
    op.execute("GRANT SELECT ON task_runs TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON task_runs FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS task_runs")
