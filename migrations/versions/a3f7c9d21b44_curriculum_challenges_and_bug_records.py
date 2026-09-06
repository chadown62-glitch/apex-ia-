"""curriculum_challenges + bug_records — persistance du Curriculum
Challenge Model et du Bug Farm (passe « Adversarial Curriculum + Capability
Benchmarking »)

Revision ID: a3f7c9d21b44
Revises: be2826e5de10
Create Date: 2026-09-06

`curriculum_challenges` : une ligne = un `apex.metalearner.challenge_model.
CurriculumChallenge`, référence une `Task` RÉELLEMENT créée (`task_id`),
jamais un résultat attendu fabriqué (§2 de la mission N+10). INSERT seul —
même garantie que `judge_learning_signals`.

`bug_records` : une ligne = un `apex.metalearner.bug_farm_model.BugRecord`
(§7 de la mission) — la SEULE table de cette passe dont le statut
(`open`/`fixed`/`regressed`) est MUTABLE après création, pour pouvoir
vérifier qu'un bug corrigé ne revient pas.

Permissions, même motif que `gauntlet_results`/`judge_learning_signals` :
seul CORE écrit (produit exclusivement à l'intérieur de
`PopulationEvolutionService`, jamais un clone).
- `curriculum_challenges` : `apex_core` `SELECT, INSERT` (UPDATE/DELETE/
  TRUNCATE révoqués) ; `apex_readonly` `SELECT` seul.
- `bug_records` : `apex_core` `SELECT, INSERT, UPDATE` (le statut évolue
  après création — DELETE/TRUNCATE révoqués, un bug n'est jamais supprimé) ;
  `apex_readonly` `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c9d21b44"
down_revision: str | None = "be2826e5de10"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curriculum_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("weakness_id", sa.String(128), nullable=False),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("evaluation_criteria", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("heldout", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("adversarial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("protocol_version", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX curriculum_challenges_specialty_idx ON curriculum_challenges (specialty)")
    op.execute("CREATE INDEX curriculum_challenges_weakness_id_idx ON curriculum_challenges (weakness_id)")
    op.execute("CREATE INDEX curriculum_challenges_dimension_idx ON curriculum_challenges (dimension)")
    op.execute("CREATE INDEX curriculum_challenges_generation_idx ON curriculum_challenges (generation)")
    op.execute("CREATE INDEX curriculum_challenges_task_id_idx ON curriculum_challenges (task_id)")
    op.execute("CREATE INDEX curriculum_challenges_source_idx ON curriculum_challenges (source)")
    op.execute("CREATE INDEX curriculum_challenges_created_at_idx ON curriculum_challenges (created_at)")

    op.execute("GRANT SELECT, INSERT ON curriculum_challenges TO apex_core")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON curriculum_challenges FROM apex_core")
    op.execute("GRANT SELECT ON curriculum_challenges TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON curriculum_challenges FROM apex_readonly")

    op.create_table(
        "bug_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("weakness_id", sa.String(128), nullable=False, unique=True),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("failure_category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("first_seen_generation", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("member_id", sa.String(36), nullable=False),
        sa.Column("genome_version_id", sa.String(36), nullable=False),
        sa.Column("evidence_reference", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("fixed_at_generation", sa.Integer(), nullable=True),
        sa.Column("regressed_at_generation", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
    )
    op.execute("CREATE INDEX bug_records_specialty_idx ON bug_records (specialty)")
    op.execute("CREATE INDEX bug_records_weakness_id_idx ON bug_records (weakness_id)")
    op.execute("CREATE INDEX bug_records_dimension_idx ON bug_records (dimension)")
    op.execute("CREATE INDEX bug_records_created_at_idx ON bug_records (created_at)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON bug_records TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON bug_records FROM apex_core")
    op.execute("GRANT SELECT ON bug_records TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON bug_records FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bug_records")
    op.execute("DROP TABLE IF EXISTS curriculum_challenges")
