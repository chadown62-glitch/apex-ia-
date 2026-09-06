"""gauntlet_results — persistance des décisions du Judge System (passe
« Strict Judge System + Gauntlet Learning Loop »)

Revision ID: 9cafd4f90c50
Revises: dfd9571d6020
Create Date: 2026-09-06

Une ligne = UNE décision agrégée, déterministe, d'un ou plusieurs juges
spécialisés sur UN membre de population, à UN stage (`cheap`/`full` — voir
`apex.metalearner.judge_model.GauntletStage`). `judge_results` (JSON) porte
la liste STRUCTURÉE des jugements individuels — jamais seulement un texte
narratif (§10 de la mission : une décision doit être traçable jusqu'à ses
preuves).

INSERT seul — un jugement, une fois produit, n'est jamais modifié ni
supprimé (même garantie que `eval_runs`/`population_members.fitness`).

Permissions, même motif que `population_members`/`learning_sessions` :
seul CORE écrit (le Judge System s'exécute exclusivement à l'intérieur
d'`apex.metalearner.evolution.PopulationEvolutionService`, jamais un
clone).
- `apex_core` : `SELECT, INSERT` — `UPDATE`/`DELETE`/`TRUNCATE` révoqués :
  un jugement n'est jamais modifié après coup.
- `apex_readonly` : `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès — même invariant
  absolu qu'`agents`/`genome_versions`/`population_members` (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9cafd4f90c50"
down_revision: str | None = "dfd9571d6020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gauntlet_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("protocol_version", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("overall_confidence", sa.Float(), nullable=True),
        sa.Column("veto", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("veto_dimension", sa.String(32), nullable=True),
        sa.Column("disagreement", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stopped_after_critical_failure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("judge_results", sa.JSON(), nullable=False),
        sa.Column("compared_champion_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX gauntlet_results_specialty_idx ON gauntlet_results (specialty)")
    op.execute("CREATE INDEX gauntlet_results_generation_idx ON gauntlet_results (generation)")
    op.execute("CREATE INDEX gauntlet_results_member_id_idx ON gauntlet_results (member_id)")
    op.execute("CREATE INDEX gauntlet_results_stage_idx ON gauntlet_results (stage)")
    op.execute("CREATE INDEX gauntlet_results_decision_idx ON gauntlet_results (decision)")
    op.execute("CREATE INDEX gauntlet_results_created_at_idx ON gauntlet_results (created_at)")

    op.execute("GRANT SELECT, INSERT ON gauntlet_results TO apex_core")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON gauntlet_results FROM apex_core")
    op.execute("GRANT SELECT ON gauntlet_results TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON gauntlet_results FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gauntlet_results")
