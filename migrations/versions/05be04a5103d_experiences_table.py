"""experiences — persistance de la boucle d'apprentissage adaptative (N+20)

Revision ID: 05be04a5103d
Revises: b7c9d1e3f5a2
Create Date: 2026-09-13

`experiences` INSERT seul, même garantie que `tasks`/`eval_runs`
(`UPDATE`/`DELETE`/`TRUNCATE` révoqués pour tous les rôles applicatifs) —
voir la docstring d'`ExperienceRow` (`apex/db/models.py`) pour la
distinction avec `eval_runs` (résultat brut vs. interprétation pour la
boucle d'apprentissage : fitness, catégorie d'échec, généalogie de
génération).

Permissions, même motif que `eval_runs` (migration `eda413ca0829`) :
- `apex_core` (`MetaLearner`/boucle adaptative écrit pour son compte) :
  `SELECT, INSERT`.
- `apex_readonly` : `SELECT` seul.
- Aucun autre rôle applicatif n'a besoin d'accès à cette table à ce jour
  (contrairement à `eval_runs`, `apex_eval` n'a pas de raison d'écrire une
  interprétation de génération — cette écriture reste un acte du niveau
  ORCHESTRATEUR de la boucle adaptative, jamais de l'exécuteur d'une seule
  tentative).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "05be04a5103d"
down_revision: str | None = "b7c9d1e3f5a2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("eval_run_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("generation_number", sa.Integer(), nullable=False),
        sa.Column("generation_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("solution_fingerprint", sa.String(64), nullable=True),
        sa.Column("execution_status", sa.String(32), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("oracle_verdict", sa.String(16), nullable=False),
        sa.Column("fitness", sa.Float(), nullable=False),
        sa.Column("failure_category", sa.String(32), nullable=False),
        sa.Column("parent_experience_id", sa.String(36), nullable=True),
        sa.Column("parent_generation", sa.Integer(), nullable=True),
        sa.Column("mutation_description", sa.Text(), nullable=True),
        sa.Column("improvement_delta", sa.Float(), nullable=True),
        sa.Column("experiment_ref", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX experiences_task_id_idx ON experiences (task_id)")
    op.execute("CREATE INDEX experiences_eval_run_id_idx ON experiences (eval_run_id)")
    op.execute("CREATE INDEX experiences_candidate_id_idx ON experiences (candidate_id)")
    op.execute("CREATE INDEX experiences_model_id_idx ON experiences (model_id)")
    op.execute("CREATE INDEX experiences_strategy_id_idx ON experiences (strategy_id)")
    op.execute("CREATE INDEX experiences_generation_number_idx ON experiences (generation_number)")
    op.execute("CREATE INDEX experiences_oracle_verdict_idx ON experiences (oracle_verdict)")
    op.execute("CREATE INDEX experiences_failure_category_idx ON experiences (failure_category)")
    op.execute("CREATE INDEX experiences_parent_experience_id_idx ON experiences (parent_experience_id)")
    op.execute("CREATE INDEX experiences_experiment_ref_idx ON experiences (experiment_ref)")
    op.execute("CREATE INDEX experiences_created_at_idx ON experiences (created_at)")

    op.execute("GRANT SELECT, INSERT ON experiences TO apex_core")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON experiences FROM apex_core")
    op.execute("GRANT SELECT ON experiences TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON experiences FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS experiences")
