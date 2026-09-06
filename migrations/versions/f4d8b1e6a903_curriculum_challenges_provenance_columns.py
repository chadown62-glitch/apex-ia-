"""curriculum_challenges — colonnes de provenance parent_task_id/seed
(passe « Universal Task + Curriculum Engine », N+11)

Revision ID: f4d8b1e6a903
Revises: a3f7c9d21b44
Create Date: 2026-09-06

Extension ADDITIVE de `curriculum_challenges` (§23 de la mission N+11 :
« réutiliser les tables existantes quand elles suffisent, ne pas dupliquer
CurriculumChallenge ») — deux colonnes NULLABLES, aucune ligne existante
affectée :

- `parent_task_id` : référence la tâche transformée par
  `apex.taskgen.variant_engine.VariantEngine` (§6/§16) — `NULL` pour tout
  challenge produit avant cette passe (comportement N+10 inchangé).
- `seed` : la graine déterministe de la sélection de variante (§6) —
  `NULL` de la même façon.

Aucun changement de permission : les GRANT/REVOKE existants sur
`curriculum_challenges` (migration `a3f7c9d21b44`) couvrent déjà ces
nouvelles colonnes (un GRANT sur la table entière, pas colonne par
colonne)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4d8b1e6a903"
down_revision: str | None = "a3f7c9d21b44"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("curriculum_challenges", sa.Column("parent_task_id", sa.String(36), nullable=True))
    op.add_column("curriculum_challenges", sa.Column("seed", sa.Integer(), nullable=True))
    op.execute(
        "CREATE INDEX curriculum_challenges_parent_task_id_idx "
        "ON curriculum_challenges (parent_task_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS curriculum_challenges_parent_task_id_idx")
    op.drop_column("curriculum_challenges", "seed")
    op.drop_column("curriculum_challenges", "parent_task_id")
