"""skills — persistance SkillRegistry (phase 14)

Revision ID: 10b43e7aec5f
Revises: eda413ca0829
Create Date: 2026-08-30

Une compétence versionnée, distincte du Genome (`docs/VISION_INTEGRATION.md`
§2.2 : « une compétence a son propre cycle de promotion, ses benchmarks et sa
généalogie »). Ce cycle de promotion est HUMAIN, ici, jamais celui de
`PromotionGovernor` (PLANNED, phase 16, qui gouverne la promotion vers le
Core, un sujet entièrement différent).

Permissions, motif spécifique à cette étape :
- `apex_core` : `SELECT, INSERT, UPDATE` — INSERT pour `propose()`, UPDATE
  pour `decide()` (le SEUL usage réel de cet UPDATE au niveau service :
  `apex/db/repositories.py::SkillRepository.decide()` ne mute QUE les
  champs de décision, jamais le contenu de la compétence). `DELETE`/
  `TRUNCATE` révoqués : une compétence n'est jamais supprimée, une révision
  est une nouvelle ligne (`supersedes_id`), même garantie que
  `memory_records`.
- `apex_readonly` : `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès — un clone n'écrit
  jamais sa propre compétence (§8 anti-auto-illusion), seul le service le
  fait pour son compte, cohérent avec `MissionRow`/`memory_records`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "10b43e7aec5f"
down_revision: str | None = "eda413ca0829"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prerequisite_skill_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence_run_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", sa.String(16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )
    op.execute("CREATE INDEX skills_name_idx ON skills (name)")
    op.execute("CREATE INDEX skills_specialty_idx ON skills (specialty)")
    op.execute("CREATE INDEX skills_status_idx ON skills (status)")
    op.execute("CREATE INDEX skills_supersedes_id_idx ON skills (supersedes_id)")
    op.execute("CREATE INDEX skills_created_at_idx ON skills (created_at)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON skills TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON skills FROM apex_core")
    op.execute("GRANT SELECT ON skills TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON skills FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS skills")
