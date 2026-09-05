"""tasks.expected_answer + eval_runs.model_id / nullable clone_id+sandbox_status
— passe « Real Model Evaluation »

Revision ID: e7f9a1c3b5d7
Revises: d4f6a8c0e2b3
Create Date: 2026-09-05

Persiste le premier chemin additif reliant `EvalRunner` à `ModelProvider`
(`EvalRunnerService.run_model_eval`, distinct de `run()` — le chemin
déterministe existant — jamais un remplacement) :

- `tasks.expected_answer` (NULLABLE) : réponse attendue en texte libre,
  consultée uniquement par `apex.oracles.response_judge.
  ExactMatchResponseJudge`. `NULL` par défaut — aucune tâche existante n'a
  besoin de ce champ, aucun backfill.
- `eval_runs.model_id` (NULLABLE, ajouté) : nom réellement renvoyé par le
  backend (`GenerationResult.model_used`), même discipline que
  `AgentExecutionRow.model_id` — `NULL` tant qu'aucune génération n'a
  réellement abouti.
- `eval_runs.clone_id` / `eval_runs.sandbox_status` (relâchés en NULLABLE,
  étaient NOT NULL) : un `EvalRun` du chemin `ModelProvider` n'a
  structurellement ni Clone exécutant ni sandbox — voir
  `apex.db.models.EvalRunRow` pour le raisonnement complet. Élargissement
  pur (NOT NULL -> NULLABLE) : aucune ligne existante n'est modifiée, aucun
  risque de perte de données. `downgrade()` ne peut réussir que si aucune
  ligne `eval_runs` n'a de `clone_id`/`sandbox_status` NULL au moment du
  rollback (même limite honnête que les migrations précédentes qui ne
  gèrent pas de backfill rétroactif).

Aucun GRANT/REVOKE supplémentaire nécessaire : les permissions déjà
accordées sur `tasks`/`eval_runs` (migration `eda413ca0829`) sont au niveau
table, elles couvrent implicitement ces nouvelles colonnes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f9a1c3b5d7"
down_revision: str | None = "d4f6a8c0e2b3"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("expected_answer", sa.Text(), nullable=True))

    op.add_column("eval_runs", sa.Column("model_id", sa.String(200), nullable=True))
    op.execute("CREATE INDEX eval_runs_model_id_idx ON eval_runs (model_id)")
    op.alter_column("eval_runs", "clone_id", existing_type=sa.String(36), nullable=True)
    op.alter_column("eval_runs", "sandbox_status", existing_type=sa.String(32), nullable=True)


def downgrade() -> None:
    op.alter_column("eval_runs", "sandbox_status", existing_type=sa.String(32), nullable=False)
    op.alter_column("eval_runs", "clone_id", existing_type=sa.String(36), nullable=False)
    op.execute("DROP INDEX IF EXISTS eval_runs_model_id_idx")
    op.drop_column("eval_runs", "model_id")

    op.drop_column("tasks", "expected_answer")
