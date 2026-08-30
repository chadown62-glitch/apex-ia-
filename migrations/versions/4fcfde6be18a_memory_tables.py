"""memory_records + memory_relations — persistance MemoryProvider (étape 9)

Revision ID: 4fcfde6be18a
Revises: 77d1c2747be8
Create Date: 2026-08-29

Tranche minimale et réelle (docs/COGNITIVE_ARCHITECTURE.md §6) : un
enregistrement avec sa provenance/confiance/statut de vérification, et une
relation SUPPORTS/CONTRADICTS entre deux enregistrements EXISTANTS. Pas de
RAG/embeddings, pas de graphe visuel, pas de moteur de traversée.

Permissions, même motif que `missions`/`agents` : seuls `apex_core` (le
MemoryService écrit/lit pour son compte — PolicyEngine n'autorise
WRITE_OWN_MEMORY/PROMOTE_MEMORY_TO_SHARED/READ_SHARED_MEMORY qu'aux sujets
déjà déclarés dans son allow-list, jamais modifiée par cette étape) et
`apex_readonly` touchent ces deux tables. Aucun rôle de clone (`apex_agent`,
`apex_eval`, `apex_audit`) n'y a accès direct : un clone n'écrit jamais sa
propre ligne, seul le service le fait pour son compte — cohérent avec
`AgentRuntime`/`MissionService`.

`UPDATE` reste accordé à `apex_core` (nécessaire à `promote_to_shared`, la
SEULE mutation autorisée — la visibilité, jamais le contenu) ; `DELETE`/
`TRUNCATE` sont révoqués : aucune ligne n'est jamais supprimée depuis
`apex.memory` (INSERT + un UPDATE ciblé seuls exposés par
`apex.db.repositories.MemoryRepository`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4fcfde6be18a"
down_revision: str | None = "77d1c2747be8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("domain", sa.String(64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verification_status", sa.String(16), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("evidence_for", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence_against", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("external_provenance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("external_source", sa.String(256), nullable=True),
        sa.Column("supersedes_id", sa.String(36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX memory_records_kind_idx ON memory_records (kind)")
    op.execute("CREATE INDEX memory_records_verification_status_idx ON memory_records (verification_status)")
    op.execute("CREATE INDEX memory_records_visibility_idx ON memory_records (visibility)")
    op.execute("CREATE INDEX memory_records_supersedes_id_idx ON memory_records (supersedes_id)")
    op.execute("CREATE INDEX memory_records_created_at_idx ON memory_records (created_at)")

    op.create_table(
        "memory_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX memory_relations_source_id_idx ON memory_relations (source_id)")
    op.execute("CREATE INDEX memory_relations_target_id_idx ON memory_relations (target_id)")
    op.execute("CREATE INDEX memory_relations_relation_idx ON memory_relations (relation)")
    op.execute("CREATE INDEX memory_relations_created_at_idx ON memory_relations (created_at)")

    for table in ("memory_records", "memory_relations"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO apex_core")
        op.execute(f"REVOKE DELETE, TRUNCATE ON {table} FROM apex_core")
        op.execute(f"GRANT SELECT ON {table} TO apex_readonly")
        op.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {table} FROM apex_readonly")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_relations")
    op.execute("DROP TABLE IF EXISTS memory_records")
