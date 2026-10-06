"""tasks.kind / workspace_seed — tâches « project » (plusieurs fichiers)

Revision ID: a7c9e1b3d5f7
Revises: f2a4c6e8b0d1
Create Date: 2026-10-06

`kind` : type de tâche (`function` | `project`, voir `apex.tasks.model.TaskKind`) ; NULL pour les tâches
existantes (traitées comme `function` si elles portent un harnais — aucune ligne réécrite). `workspace_seed` :
fichiers VISIBLES de départ d'une tâche `project` (JSON chemin -> contenu). Les valeurs attendues restent dans
`expected_answer`, comme pour les tâches `function`. Permissions de la table inchangées.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1b3d5f7"
down_revision: str | None = "f2a4c6e8b0d1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("kind", sa.String(16), nullable=True))
    op.add_column("tasks", sa.Column("workspace_seed", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "workspace_seed")
    op.drop_column("tasks", "kind")
