"""agent_executions_model_id_idx — passe « Adaptive Model Router V1 —
Hardening »

Revision ID: d4f6a8c0e2b3
Revises: c3e5f7a9b1d2
Create Date: 2026-09-02

`apex.db.repositories.AgentExecutionRepository.list_by_model` (ajoutée
passe « Adaptive Model Router V1 ») filtre `WHERE model_id = :x` sans
qu'aucun index ne couvre cette colonne — les index déjà présents sur
`agent_executions` (`agent_id`/`mission_id`/`status`/`started_at`, migration
`b2d4e6f8a0c1`) ne le font pas non plus. Relevé par la review R1 comme
dette technique réelle (§R12/R10) : un scan complet de la table à chaque
appel, dégradation progressive avec le volume — corrigé ici avant tout
câblage réel du mécanisme adaptatif (`performance=` reste non fourni par
aucun appelant de production, cette migration ne change donc aucun
comportement observable, seulement une capacité de lecture pas encore
exercée).

Même motif que `agent_executions_started_at_idx` : un index simple, pas
composite — `list_by_model` trie déjà sur `(started_at DESC, id)` en
mémoire côté requête (clause `ORDER BY` de la requête elle-même), un index
composite `(model_id, started_at)` serait une optimisation plus poussée que
ce que le volume actuel (aucun call site réel n'alimente encore cette
colonne en production) justifie — à reconsidérer avec des données réelles,
pas en anticipation.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4f6a8c0e2b3"
down_revision: str | None = "c3e5f7a9b1d2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX agent_executions_model_id_idx ON agent_executions (model_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS agent_executions_model_id_idx")
