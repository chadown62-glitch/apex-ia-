"""genome_versions — persistance GenomeStore (phase 17)

Revision ID: 749e6b764126
Revises: 10b43e7aec5f
Create Date: 2026-08-30

Métadonnées SEULEMENT — le contenu du Genome (« données versionnées
mutables, jamais de code », `docs/COGNITIVE_ARCHITECTURE.md` §1) est stocké
par `ArtifactStoreProvider` (STUB réel depuis la Phase 10), adressé par
contenu. `content_hash` est le seul lien entre une ligne ici et le contenu
réel — exactement le `genome_ref: hash` déjà spécifié pour `Clone` (§2.2).

Permissions, motif identique à `skills` (Phase 14) : cycle de décision
HUMAIN provisoire en attendant `PromotionGovernor` (PLANNED, phase 16,
périmètre jugé insuffisamment défini pour l'instant).
- `apex_core` : `SELECT, INSERT, UPDATE` — INSERT pour `propose()`, UPDATE
  pour `decide()` (SEUL usage réel, champs de décision uniquement).
  `DELETE`/`TRUNCATE` révoqués : une version de Genome n'est jamais
  supprimée, une révision est une nouvelle ligne (généalogie par
  `parent_version_id`/`parent_version_id_b`).
- `apex_readonly` : `SELECT` seul.
- `apex_agent`/`apex_eval`/`apex_audit` : aucun accès — invariant absolu
  (ADR-0001, `docs/COGNITIVE_ARCHITECTURE.md` §1 : « aucun clone, aucune
  boucle d'apprentissage... ne modifie le Core » — un clone ne doit même
  pas pouvoir PROPOSER une version de Genome).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "749e6b764126"
down_revision: str | None = "10b43e7aec5f"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "genome_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("parent_version_id", sa.String(36), nullable=True),
        sa.Column("parent_version_id_b", sa.String(36), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", sa.String(16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )
    op.execute("CREATE INDEX genome_versions_content_hash_idx ON genome_versions (content_hash)")
    op.execute("CREATE INDEX genome_versions_parent_version_id_idx ON genome_versions (parent_version_id)")
    op.execute("CREATE INDEX genome_versions_parent_version_id_b_idx ON genome_versions (parent_version_id_b)")
    op.execute("CREATE INDEX genome_versions_status_idx ON genome_versions (status)")
    op.execute("CREATE INDEX genome_versions_created_at_idx ON genome_versions (created_at)")
    op.execute("CREATE INDEX genome_versions_decided_at_idx ON genome_versions (decided_at)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON genome_versions TO apex_core")
    op.execute("REVOKE DELETE, TRUNCATE ON genome_versions FROM apex_core")
    op.execute("GRANT SELECT ON genome_versions TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON genome_versions FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS genome_versions")
