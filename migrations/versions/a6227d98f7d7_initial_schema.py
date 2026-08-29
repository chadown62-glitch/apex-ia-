"""initial schema — compute_pools, events (partitioned), audit_log, rôles applicatifs

Revision ID: a6227d98f7d7
Revises:
Create Date: 2026-08-29

Portée exacte du brief phase 2 (docs/VISION_INTEGRATION.md §5) : ComputePool
(table + trust_level), EventSink (table events, partitionnée mensuellement —
c'est la seule des cinq tables nommées par docs/PHASE_1_REPORT.md §6 qui
appartient à une abstraction déjà implémentée ; task_runs/model_runs/
tool_usage/sandbox_execs sont délibérément absentes ici, voir
apex/db/models.py), audit_log en écriture seule pour les rôles applicatifs.

Rôles créés (scripts/init-db/01-extensions.sql annonçait ce report) :
apex_core, apex_agent, apex_eval, apex_audit, apex_readonly. Chacun avec un
mot de passe lu depuis APEX_DB_<ROLE>_PASSWORD au moment de la migration —
jamais une valeur en dur (§72). La migration échoue si une variable manque :
pas de mot de passe par défaut pour un rôle qui touche à la base réelle.

Partitionnement natif de `events` : la contrainte PostgreSQL pour une table
partitionnée est que toute contrainte UNIQUE/PK doit inclure la colonne de
partitionnement (`ts`). Plutôt que forcer une clé composite (id, ts) jusque
dans le modèle SQLAlchemy — ce qui casserait l'auto-incrément portable côté
SQLite utilisé par les tests génériques — `id` est alimenté par une séquence
dédiée (unicité garantie en pratique) et la seule contrainte SQL est
UNIQUE(id, ts), qui satisfait la règle de partitionnement sans imposer une
clé primaire composite. C'est un compromis documenté, pas un oubli.

Partitions créées : le mois courant + 2 mois suivants, plus une partition
DEFAULT pour ne jamais faire échouer un INSERT hors plage. La création des
partitions futures est un travail opérationnel non couvert par cette
migration (voir docs/PHASE_2_REPORT.md, section risques).
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6227d98f7d7"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

APPLICATION_ROLES = ("apex_core", "apex_agent", "apex_eval", "apex_audit", "apex_readonly")


def _role_password(role: str) -> str:
    var = f"APEX_DB_{role.removeprefix('apex_').upper()}_PASSWORD"
    password = os.environ.get(var)
    if not password:
        raise RuntimeError(
            f"variable d'environnement « {var} » non définie — "
            f"aucun mot de passe par défaut pour un rôle applicatif (§72)"
        )
    return password.replace("'", "''")


def upgrade() -> None:
    # -- Rôles applicatifs ---------------------------------------------------
    for role in APPLICATION_ROLES:
        password = _role_password(role)
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} WITH LOGIN PASSWORD '{password}';
                ELSE
                    ALTER ROLE {role} WITH LOGIN PASSWORD '{password}';
                END IF;
            END
            $$;
            """
        )

    # -- compute_pools --------------------------------------------------------
    op.create_table(
        "compute_pools",
        sa.Column("pool_id", sa.String(64), primary_key=True),
        sa.Column("location", sa.String(16), nullable=False),
        sa.Column("trust_level", sa.String(16), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("cpu_cores", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ram_gb", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_per_hour", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -- events : table partitionnée par mois sur ts (raw DDL, voir docstring) --
    op.execute("CREATE SEQUENCE events_id_seq")
    op.execute(
        """
        CREATE TABLE events (
            id BIGINT NOT NULL DEFAULT nextval('events_id_seq'),
            ts TIMESTAMPTZ NOT NULL,
            level VARCHAR(16) NOT NULL,
            logger VARCHAR(128) NOT NULL,
            msg TEXT NOT NULL,
            context JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (id, ts)
        ) PARTITION BY RANGE (ts)
        """
    )
    op.execute("CREATE INDEX events_ts_idx ON events (ts)")
    _create_monthly_partitions("events", months_ahead=2)

    # -- audit_log : NON partitionnée (absente de la liste des 5 tables à
    #    forte cardinalité de docs/PHASE_1_REPORT.md §6) -----------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target", sa.String(256), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("CREATE INDEX audit_log_ts_idx ON audit_log (ts)")

    # -- Privilèges minimaux, explicites --------------------------------------
    # Par défaut PostgreSQL n'accorde rien à un rôle fraîchement créé : les
    # GRANT ci-dessous sont donc la totalité des permissions, mais les REVOKE
    # explicites documentent l'intention et protègent contre un GRANT ALTER
    # ultérieur par erreur qui élargirait silencieusement l'accès.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON compute_pools TO apex_core")
    op.execute("GRANT SELECT, INSERT ON events TO apex_core")
    op.execute("GRANT USAGE ON SEQUENCE events_id_seq TO apex_core")
    op.execute("GRANT SELECT ON audit_log TO apex_core")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM apex_core")

    op.execute("GRANT SELECT ON compute_pools TO apex_agent")
    op.execute("GRANT INSERT ON events TO apex_agent")
    op.execute("GRANT USAGE ON SEQUENCE events_id_seq TO apex_agent")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON events FROM apex_agent")

    op.execute("GRANT SELECT ON compute_pools TO apex_eval")
    op.execute("GRANT SELECT ON events TO apex_eval")

    op.execute("GRANT INSERT ON audit_log TO apex_audit")
    op.execute("GRANT USAGE ON SEQUENCE audit_log_id_seq TO apex_audit")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM apex_audit")

    op.execute("GRANT SELECT ON compute_pools, events, audit_log TO apex_readonly")
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON compute_pools, events, audit_log FROM apex_readonly")
    # Aucun GRANT USAGE sur les séquences pour apex_readonly/apex_eval : sans lui,
    # même une session compromise avec un GRANT INSERT accidentel ne pourrait
    # pas produire d'id — défense en profondeur, pas seulement TABLE-level.


def _create_monthly_partitions(table: str, *, months_ahead: int) -> None:
    import datetime

    today = datetime.date.today().replace(day=1)
    months = []
    year, month = today.year, today.month
    for _ in range(months_ahead + 1):
        months.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1

    for year, month in months:
        start = f"{year:04d}-{month:02d}-01"
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        end = f"{next_year:04d}-{next_month:02d}-01"
        partition_name = f"{table}_{year:04d}_{month:02d}"
        op.execute(
            f"CREATE TABLE {partition_name} PARTITION OF {table} "
            f"FOR VALUES FROM ('{start}') TO ('{end}')"
        )
    op.execute(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute(f"DROP TABLE IF EXISTS events")  # supprime aussi les partitions (CASCADE implicite pour les enfants)
    op.execute("DROP SEQUENCE IF EXISTS events_id_seq")
    op.drop_table("compute_pools")
    for role in APPLICATION_ROLES:
        op.execute(f"DROP ROLE IF EXISTS {role}")
