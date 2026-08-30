"""missions.depends_on — dépendances entre missions (étape 8, Scheduler)

Revision ID: 77d1c2747be8
Revises: 218c33298ccd
Create Date: 2026-08-29

Colonne JSON nullable=False, défaut `[]` — jamais NULL, pour éviter un cas
particulier côté lecture (`apex.missions.model.Mission.depends_on` est un
tuple, jamais `None`). Fixée à la création d'une mission, jamais mutée
ensuite (voir `apex.missions.dependencies` pour l'invariant qui rend un
cycle impossible par construction).

Aucun changement de permission nécessaire : cette colonne appartient à la
table `missions` déjà couverte par les GRANT/REVOKE de la migration
218c33298ccd (apex_core en écriture, apex_readonly en lecture seule).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "77d1c2747be8"
down_revision: str | None = "218c33298ccd"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("depends_on", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("missions", "depends_on")
