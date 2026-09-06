"""population_members — persistance Population (passe « Real Clone
Population + Evolution Loop »)

Revision ID: 796625d237c6
Revises: e7f9a1c3b5d7
Create Date: 2026-09-05

Une "population" n'est pas une table séparée : c'est l'ensemble des lignes
qui partagent `specialty`, ajoutées/décidées au fil des générations d'
`apex.evolution.service.PopulationEvolutionService`. Chaque ligne EST un
`Clone` réel (`agents.clone_id`) exécuté avec une `GenomeVersion` réelle
(`genome_versions.id`) — aucune troisième représentation dupliquée, voir
`apex.db.models.PopulationMemberRow`.

`experiment_ref` porte l'idempotence (mandat §17) : `UNIQUE`, vérifiée par
le domaine AVANT toute création (`PopulationMemberRepository.
get_by_experiment_ref`), jamais une contrainte silencieusement heurtée.

Permissions, même motif que `agents`/`genome_versions` : seul CORE
écrit (spawn/mutation/sélection automatiques, jamais un clone lui-même).
- `apex_core` : `SELECT, INSERT, UPDATE` — `UPDATE` limité en pratique à
  `fitness`/`fitness_components`/`skill_candidate_id`/`genome_candidate_id`/
  `status`/`decided_at` (`PopulationMemberRepository.record_fitness`/
  `set_status`, jamais une réécriture arbitraire). `DELETE`/`TRUNCATE`
  révoqués : un membre retiré (`status='retired'`) n'est jamais supprimé,
  l'historique/la généalogie restent permanents.
- `apex_readonly` : `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès — même invariant
  absolu qu'`agents`/`genome_versions` (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "796625d237c6"
down_revision: str | None = "e7f9a1c3b5d7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "population_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("clone_id", sa.String(36), nullable=False),
        sa.Column("genome_version_id", sa.String(36), nullable=False),
        sa.Column("parent_member_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fitness", sa.Float(), nullable=True),
        sa.Column("fitness_components", sa.JSON(), nullable=True),
        sa.Column("skill_candidate_id", sa.String(36), nullable=True),
        sa.Column("genome_candidate_id", sa.String(36), nullable=True),
        sa.Column("experiment_ref", sa.String(160), nullable=False, unique=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE INDEX population_members_specialty_idx ON population_members (specialty)")
    op.execute("CREATE INDEX population_members_generation_idx ON population_members (generation)")
    op.execute("CREATE INDEX population_members_clone_id_idx ON population_members (clone_id)")
    op.execute(
        "CREATE INDEX population_members_genome_version_id_idx ON population_members (genome_version_id)"
    )
    op.execute(
        "CREATE INDEX population_members_parent_member_id_idx ON population_members (parent_member_id)"
    )
    op.execute("CREATE INDEX population_members_status_idx ON population_members (status)")
    op.execute("CREATE INDEX population_members_created_at_idx ON population_members (created_at)")
    op.execute("CREATE INDEX population_members_experiment_ref_idx ON population_members (experiment_ref)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON population_members TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON population_members FROM apex_core")
    op.execute("GRANT SELECT ON population_members TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON population_members FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS population_members")
