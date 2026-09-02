"""agent_executions.elapsed_s/tokens_prompt/tokens_completion — passe
« Adaptive Model Router V1 »

Revision ID: c3e5f7a9b1d2
Revises: b2d4e6f8a0c1
Create Date: 2026-09-02

Persiste ce qu'`apex.models.model.GenerationResult` mesure déjà réellement
à chaque appel (`elapsed_s`, `tokens_prompt`, `tokens_completion`) mais que
`AgentRegistryService._complete()` jetait jusqu'ici — voir
`apex.agent_registry.service` et le rapport de cette passe pour le
raisonnement complet.

Trois colonnes NULLABLE, aucun `server_default` autre que `NULL` : une ligne
`agent_executions` déjà écrite (avant cette migration, ou pour une
exécution qui n'a produit aucun `GenerationResult` — échec total, candidat
jamais tenté) doit rester `NULL` sur ces colonnes, jamais un `0` ou une
valeur devinée — même discipline que le reste de la table (`model_id`/
`provider_id` déjà `NULL` par défaut). Aucun backfill : les exécutions déjà
enregistrées n'ont jamais eu ces mesures capturées, il n'existe donc rien
de réel à y écrire rétroactivement.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5f7a9b1d2"
down_revision: str | None = "b2d4e6f8a0c1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_executions", sa.Column("elapsed_s", sa.Float(), nullable=True))
    op.add_column("agent_executions", sa.Column("tokens_prompt", sa.Integer(), nullable=True))
    op.add_column("agent_executions", sa.Column("tokens_completion", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_executions", "tokens_completion")
    op.drop_column("agent_executions", "tokens_prompt")
    op.drop_column("agent_executions", "elapsed_s")
