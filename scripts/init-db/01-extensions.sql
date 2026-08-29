-- APEX — initialisation minimale de la base (phase 1).
--
-- Périmètre volontairement restreint aux extensions. Les rôles applicatifs
-- (apex_core, apex_agent, apex_eval, apex_audit, apex_readonly), les tables et
-- la contrainte INSERT-only sur audit_log arrivent en phase 2, via Alembic.
-- Créer des rôles ici les rendrait invisibles aux migrations et non testables.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector : recherche sémantique
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- recherche lexicale / fuzzy
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DO $$
BEGIN
  RAISE NOTICE 'APEX: extensions initialisées (vector, pg_trgm, pg_stat_statements)';
END
$$;
