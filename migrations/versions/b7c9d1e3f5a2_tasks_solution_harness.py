"""tasks.solution_harness — passe « Generated-Solution Evaluation Loop » (N+19)

Revision ID: b7c9d1e3f5a2
Revises: f4d8b1e6a903
Create Date: 2026-09-13

Ajoute la colonne portant le template de script (marqueur de substitution du
code candidat généré par un `ModelProvider` + tests cachés) consulté
UNIQUEMENT par `EvalRunnerService.run_generated_solution` — un TROISIÈME
chemin d'`EvalRunner`, distinct de `run()` (script déterministe fixe) et de
`run_model_eval()` (jugement par comparaison de texte, jamais sandboxé).

`NULLABLE`, sans valeur par défaut : aucune tâche existante n'a besoin de ce
champ, aucun backfill. Aucun GRANT/REVOKE supplémentaire nécessaire : les
permissions déjà accordées sur `tasks` (migration `eda413ca0829`) sont au
niveau table, elles couvrent implicitement cette nouvelle colonne.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c9d1e3f5a2"
down_revision: str | None = "f4d8b1e6a903"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("solution_harness", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "solution_harness")
