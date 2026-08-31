"""tasks + eval_runs — persistance TaskBank/EvalRunner (phase 5)

Revision ID: eda413ca0829
Revises: 4fcfde6be18a
Create Date: 2026-08-30

Premier véritable laboratoire expérimental d'APEX (`docs/VISION_INTEGRATION.md`
§5) : une tâche d'évaluation (`tasks`) et un résultat d'évaluation
(`eval_runs`), tous deux INSERT seul — `UPDATE`/`DELETE`/`TRUNCATE` révoqués
pour TOUS les rôles applicatifs, y compris `apex_core` (plus strict que
`missions`/`memory_records` : une tâche ou un résultat ne change jamais après
coup, une correction est une nouvelle ligne).

Permissions, motif spécifique à cette étape :
- `apex_core` (TaskBankService / EvalRunnerService écrivent pour leur
  compte) : `SELECT, INSERT` sur les deux tables.
- `apex_readonly` : `SELECT` sur les deux tables.
- `apex_eval` (rôle réservé sans usage depuis la migration initiale,
  `a6227d98f7d7`, qui ne lui accordait que `SELECT` sur `compute_pools`/
  `events`) : `SELECT, INSERT` sur `eval_runs` SEULEMENT — **aucun accès à
  `tasks`**. C'est le mécanisme qui rend le critère HELDOUT (ADR-0010,
  différé en phase 2, `tests/test_db_security.py::TestHeldoutSplitSeparation`)
  vrai par construction : un exécuteur d'évaluation ne peut structurellement
  pas lire le contenu d'une tâche (HELDOUT ou non), qu'il connaisse ou non
  son `id` — pas une règle applicative contournable.
- `apex_agent`/`apex_audit` : aucun accès (ni GRANT ni besoin de REVOKE
  explicite, le comportement par défaut PostgreSQL pour un rôle sans GRANT
  est déjà "aucun accès" — cohérent avec `missions`/`memory_records`, qui ne
  révoquent explicitement que ce qui a été accordé).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eda413ca0829"
down_revision: str | None = "4fcfde6be18a"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("split", sa.String(16), nullable=False),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("dataset_ref", sa.String(256), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX tasks_split_idx ON tasks (split)")
    op.execute("CREATE INDEX tasks_specialty_idx ON tasks (specialty)")
    op.execute("CREATE INDEX tasks_created_at_idx ON tasks (created_at)")

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("clone_id", sa.String(36), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("dataset_ref", sa.String(256), nullable=True),
        sa.Column("experiment_ref", sa.String(128), nullable=True),
        sa.Column("oracle_verdict", sa.String(16), nullable=False),
        sa.Column("oracle_detail", sa.Text(), nullable=True),
        sa.Column("sandbox_status", sa.String(32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("elapsed_s", sa.Float(), nullable=True),
        sa.Column("facts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX eval_runs_task_id_idx ON eval_runs (task_id)")
    op.execute("CREATE INDEX eval_runs_clone_id_idx ON eval_runs (clone_id)")
    op.execute("CREATE INDEX eval_runs_method_idx ON eval_runs (method)")
    op.execute("CREATE INDEX eval_runs_experiment_ref_idx ON eval_runs (experiment_ref)")
    op.execute("CREATE INDEX eval_runs_oracle_verdict_idx ON eval_runs (oracle_verdict)")
    op.execute("CREATE INDEX eval_runs_sandbox_status_idx ON eval_runs (sandbox_status)")
    op.execute("CREATE INDEX eval_runs_created_at_idx ON eval_runs (created_at)")

    for table in ("tasks", "eval_runs"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO apex_core")
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM apex_core")
        op.execute(f"GRANT SELECT ON {table} TO apex_readonly")
        op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table} FROM apex_readonly")

    # apex_eval : accès à eval_runs SEULEMENT — jamais à tasks (critère HELDOUT, voir docstring).
    op.execute("GRANT SELECT, INSERT ON eval_runs TO apex_eval")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON eval_runs FROM apex_eval")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval_runs")
    op.execute("DROP TABLE IF EXISTS tasks")
