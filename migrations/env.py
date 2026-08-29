"""Wiring Alembic — l'URL de connexion n'est jamais dans alembic.ini (§72).

Construite depuis `config.yaml` (host/port) et l'environnement (identifiants),
via la même fonction que le reste du Core (`apex.db.engine`) : une seule
source de vérité pour "comment on se connecte à Postgres".
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from apex.config.loader import load_config
from apex.db.engine import database_url_from_config
from apex.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """Priorité : APEX_DATABASE_URL explicite, sinon config.yaml + environnement.

    L'échappement du `%` est nécessaire : `configparser` (utilisé par
    Alembic) interprète `%` comme un caractère d'interpolation.
    """
    import os

    explicit = os.environ.get("APEX_DATABASE_URL")
    if explicit:
        return explicit
    apex_config = load_config(os.environ.get("APEX_CONFIG"))
    return database_url_from_config(apex_config)


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_url().replace("%", "%%")
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
