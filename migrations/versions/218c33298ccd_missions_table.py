"""missions table — persistance Mission Engine (étape 6)

Revision ID: 218c33298ccd
Revises: 199166dcfdeb
Create Date: 2026-08-29

Créée maintenant que le domaine Mission existe réellement (apex.missions),
pas avant — même règle qu'`agents` (ADR-0007).

Permissions, même motif que `agents` : seuls `apex_core` (le Mission
Engine écrit/lit pour son compte, PolicyEngine n'autorise CREATE/CANCEL/
VALIDATE/REJECT_MISSION qu'à HUMAN, et LAUNCH_MISSION/CANCEL_MISSION qu'à
CORE) et `apex_readonly` touchent cette table. Aucun rôle de clone
(`apex_agent`, `apex_eval`, `apex_audit`) n'y a accès : un clone n'écrit
jamais sa propre ligne de mission, exactement comme il n'écrit jamais sa
propre ligne dans `agents` — cohérent avec l'allow-list de PolicyEngine
(aucun Subject de clone n'a CREATE_MISSION/VALIDATE_MISSION/REJECT_MISSION).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "218c33298ccd"
down_revision: str | None = "199166dcfdeb"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("constraints", sa.Text(), nullable=True),
        sa.Column("assigned_agent", sa.String(36), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.String(24), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE INDEX missions_state_idx ON missions (state)")
    op.execute("CREATE INDEX missions_specialty_idx ON missions (specialty)")
    op.execute("CREATE INDEX missions_created_at_idx ON missions (created_at)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON missions TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON missions FROM apex_core")
    op.execute("GRANT SELECT ON missions TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON missions FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS missions")
