"""judge_learning_signals — persistance du pont Judge -> LearningFeedback
(passe « Judge Feedback -> Weakness -> Curriculum -> Adaptive Evolution »)

Revision ID: be2826e5de10
Revises: 9cafd4f90c50
Create Date: 2026-09-06

Une ligne = le feedback structuré d'UN `JudgeResult` (`gauntlet_results`,
N+8), dans le contexte d'UN `GauntletResult` — jamais un texte narratif
seul. `category`/`failure_category` distinguent explicitement une
faiblesse RÉELLE du candidat d'une panne d'évaluation/infrastructure (§11
de la mission : « Docker indisponible ≠ candidate mauvais »).

INSERT seul — un signal, une fois produit, n'est jamais modifié ni
supprimé (l'historique complet est la SEULE source de vérité pour
l'agrégation de faiblesses, `apex.metalearner.weakness_engine`, toujours
recalculée à la demande — jamais une seconde table mutable).

Permissions, même motif que `gauntlet_results` : seul CORE écrit (produit
exclusivement à l'intérieur de `PopulationEvolutionService`, jamais un
clone).
- `apex_core` : `SELECT, INSERT` — `UPDATE`/`DELETE`/`TRUNCATE` révoqués.
- `apex_readonly` : `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "be2826e5de10"
down_revision: str | None = "9cafd4f90c50"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "judge_learning_signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("gauntlet_result_id", sa.String(36), nullable=False),
        sa.Column("member_id", sa.String(36), nullable=False),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("genome_version_id", sa.String(36), nullable=False),
        sa.Column("judge_id", sa.String(64), nullable=False),
        sa.Column("judge_version", sa.String(16), nullable=False),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("finding_summary", sa.Text(), nullable=False),
        sa.Column("evidence_reference", sa.String(36), nullable=True),
        sa.Column("protocol_version", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "CREATE INDEX judge_learning_signals_gauntlet_result_id_idx "
        "ON judge_learning_signals (gauntlet_result_id)",
    )
    op.execute("CREATE INDEX judge_learning_signals_member_id_idx ON judge_learning_signals (member_id)")
    op.execute("CREATE INDEX judge_learning_signals_specialty_idx ON judge_learning_signals (specialty)")
    op.execute("CREATE INDEX judge_learning_signals_generation_idx ON judge_learning_signals (generation)")
    op.execute(
        "CREATE INDEX judge_learning_signals_genome_version_id_idx "
        "ON judge_learning_signals (genome_version_id)",
    )
    op.execute("CREATE INDEX judge_learning_signals_dimension_idx ON judge_learning_signals (dimension)")
    op.execute("CREATE INDEX judge_learning_signals_category_idx ON judge_learning_signals (category)")
    op.execute(
        "CREATE INDEX judge_learning_signals_failure_category_idx "
        "ON judge_learning_signals (failure_category)",
    )
    op.execute("CREATE INDEX judge_learning_signals_created_at_idx ON judge_learning_signals (created_at)")

    op.execute("GRANT SELECT, INSERT ON judge_learning_signals TO apex_core")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON judge_learning_signals FROM apex_core")
    op.execute("GRANT SELECT ON judge_learning_signals TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON judge_learning_signals FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS judge_learning_signals")
