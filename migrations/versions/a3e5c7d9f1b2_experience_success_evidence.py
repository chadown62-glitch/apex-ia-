"""experiences.success_evidence — succès observé vs succès confirmé

Revision ID: a3e5c7d9f1b2
Revises: 05be04a5103d
Create Date: 2026-09-25

Une exécution réussie une seule fois n'est pas une preuve : la colonne
distingue un succès OBSERVÉ (une exécution), CONFIRMÉ (verdict reproduit sur
ré-exécution du même code) ou RÉFUTÉ (verdict différent à la ré-exécution).
Colonne nullable : les lignes existantes restent NULL, lues comme un succès
seulement observé (jamais promues à « confirmé » après coup). La table reste
INSERT seul ; aucun privilège n'est modifié.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3e5c7d9f1b2"
down_revision: str | None = "05be04a5103d"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiences", sa.Column("success_evidence", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("experiences", "success_evidence")
