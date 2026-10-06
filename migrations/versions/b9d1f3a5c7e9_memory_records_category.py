"""memory_records.category — mémoire système catégorisée (conversation, exécution, connaissances, projet)

Revision ID: b9d1f3a5c7e9
Revises: a7c9e1b3d5f7
Create Date: 2026-10-06

NULL : mémoire privée d'un clone (modèle d'origine, aucune ligne réécrite). Une valeur : mémoire SYSTÈME écrite
et lue par CORE/HUMAN seulement (`Action.WRITE_SYSTEM_MEMORY` / `READ_SYSTEM_MEMORY`). Index pour la
récupération par catégorie. Permissions de la table inchangées.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d1f3a5c7e9"
down_revision: str | None = "a7c9e1b3d5f7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_records", sa.Column("category", sa.String(32), nullable=True))
    op.create_index("memory_records_category_idx", "memory_records", ["category"])


def downgrade() -> None:
    op.drop_index("memory_records_category_idx", table_name="memory_records")
    op.drop_column("memory_records", "category")
