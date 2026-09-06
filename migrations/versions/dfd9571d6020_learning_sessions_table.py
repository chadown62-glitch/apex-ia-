"""learning_sessions — persistance de session d'apprentissage
multi-génération (passe « Autonomous Multi-Generation Learning
Orchestrator »)

Revision ID: dfd9571d6020
Revises: 796625d237c6
Create Date: 2026-09-06

Une session enveloppe des appels RÉPÉTÉS à `PopulationEvolutionService.
evolve_generation` (passe précédente, inchangée) — cette table ne porte que
la politique/le suivi de la boucle (combien de générations, laquelle est la
meilleure jusqu'ici, pourquoi elle s'est arrêtée), jamais une seconde copie
de l'état de population lui-même (`population_members` reste l'unique
source de vérité pour les membres/générations/lignage).

**Index UNIQUE partiel — la garantie d'exclusion mutuelle RÉELLE, inter-
processus** (contrairement à `CycleAlreadyRunning`,
`apex.metalearner.autonomy`, verrou mémoire documenté comme valide par
processus seulement) : au plus une ligne `running` par spécialité, imposé
par PostgreSQL/SQLite eux-mêmes, jamais par un verrou applicatif. Une
tentative de démarrage ou de reprise concurrente échoue au niveau du SGBD
(`IntegrityError`, traduite en `LearningSessionAlreadyRunning` par
`apex.metalearner.learning_session_service`).

Permissions, même motif que `population_members`/`genome_versions` : seul
CORE écrit (démarrage/reprise/progression/annulation, jamais un clone).
- `apex_core` : `SELECT, INSERT, UPDATE` — `DELETE`/`TRUNCATE` révoqués :
  une session n'est jamais supprimée, son historique reste permanent.
- `apex_readonly` : `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès — même invariant
  absolu qu'`agents`/`genome_versions`/`population_members` (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "dfd9571d6020"
down_revision: str | None = "796625d237c6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("max_generations", sa.Integer(), nullable=False),
        sa.Column("generations_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("convergence_patience", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generations_since_improvement", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_member_id", sa.String(36), nullable=True),
        sa.Column("best_fitness", sa.Float(), nullable=True),
        sa.Column("best_genome_version_id", sa.String(36), nullable=True),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE INDEX learning_sessions_specialty_idx ON learning_sessions (specialty)")
    op.execute("CREATE INDEX learning_sessions_status_idx ON learning_sessions (status)")
    op.execute("CREATE INDEX learning_sessions_created_at_idx ON learning_sessions (created_at)")
    # Exclusion mutuelle RÉELLE (voir docstring du module) — au plus UNE
    # session `running` par spécialité, appliquée par le SGBD lui-même.
    # Syntaxe d'index partiel identique en PostgreSQL et SQLite (>= 3.8.0,
    # utilisé par les tests) — aucune divergence entre les deux moteurs.
    op.execute(
        "CREATE UNIQUE INDEX learning_sessions_one_running_per_specialty_idx "
        "ON learning_sessions (specialty) WHERE status = 'running'"
    )

    op.execute("GRANT SELECT, INSERT, UPDATE ON learning_sessions TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON learning_sessions FROM apex_core")
    op.execute("GRANT SELECT ON learning_sessions TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON learning_sessions FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS learning_sessions")
