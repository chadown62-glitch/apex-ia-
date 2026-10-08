"""Construction du moteur SQLAlchemy et gestion de session.

Aucune connexion n'est ouverte à l'import (§53 appliqué à la DB : un module
ne doit pas avoir d'effet de bord au chargement). `build_engine` échoue
proprement avec `DependencyMissing`/`ConfigError` plutôt que de laisser
SQLAlchemy lever une exception non structurée.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from apex.config.schema import ApexConfig
from apex.errors import ConfigError

#: Variables reprises de docker-compose.yml — même contrat de secrets (§72) :
#: jamais dans config.yaml, toujours depuis l'environnement.
_ENV_USER = "POSTGRES_USER"
_ENV_PASSWORD = "POSTGRES_PASSWORD"
_ENV_DB = "POSTGRES_DB"

#: Rôles applicatifs créés par la migration initiale (docs/VISION_INTEGRATION.md
#: §3.3, scripts/init-db/01-extensions.sql). Chacun a son propre mot de passe :
#: APEX_DB_<ROLE>_PASSWORD, jamais une valeur partagée avec POSTGRES_PASSWORD.
APPLICATION_ROLES = ("apex_core", "apex_agent", "apex_eval", "apex_audit", "apex_readonly")


def database_url_for_role(
    role: str, config: ApexConfig, *, environ: dict[str, str] | None = None
) -> str:
    """URL de connexion pour un rôle applicatif restreint (pas le bootstrap superuser).

    Utilisé pour prouver qu'une politique de rôle est réellement appliquée par
    le SGBD (tests de sécurité) et, plus tard, par les composants qui doivent
    se connecter avec des privilèges minimaux plutôt qu'en superutilisateur.
    """
    if role not in APPLICATION_ROLES:
        raise ConfigError(f"rôle applicatif inconnu « {role} »", field="services.postgres")
    env = environ if environ is not None else dict(os.environ)
    endpoint = config.services.get("postgres")
    if endpoint is None:
        raise ConfigError("service « postgres » non déclaré", field="services.postgres")
    password_var = f"APEX_DB_{role.removeprefix('apex_').upper()}_PASSWORD"
    password = env.get(password_var)
    if not password:
        raise ConfigError(
            f"variable d'environnement « {password_var} » non définie", field="services.postgres"
        )
    db = env.get(_ENV_DB, "apex")
    return (
        f"postgresql+psycopg://{quote(role, safe='')}:{quote(password, safe='')}"
        f"@{endpoint.host}:{endpoint.port}/{quote(db, safe='')}"
    )


def database_url_from_config(config: ApexConfig, *, environ: dict[str, str] | None = None) -> str:
    """Construit l'URL PostgreSQL depuis `config.services.postgres` + l'environnement.

    Le mot de passe n'est jamais dans `config.yaml` : `docker-compose.yml`
    exige déjà `POSTGRES_PASSWORD` par `${VAR:?...}`. On applique la même
    règle ici plutôt que d'inventer un défaut.
    """
    env = environ if environ is not None else dict(os.environ)
    endpoint = config.services.get("postgres")
    if endpoint is None:
        raise ConfigError("service « postgres » non déclaré", field="services.postgres")

    password = env.get(_ENV_PASSWORD)
    if not password:
        raise ConfigError(
            f"variable d'environnement « {_ENV_PASSWORD} » non définie",
            field="services.postgres",
        )
    user = env.get(_ENV_USER, "apex")
    db = env.get(_ENV_DB, "apex")
    # `user`/`password`/`db` proviennent de l'environnement (jamais de
    # config.yaml, voir docstring du module) et peuvent donc contenir des
    # caractères réservés d'URI (`@ : / ? # %`, espace, ...) — sans
    # `quote(..., safe="")`, un secret réel contenant l'un de ces caractères
    # casse le parsing du DSN par psycopg/libpq (userinfo/host confondus) et
    # produit un rejet d'authentification alors que le secret est correct
    # (bug réel reproduit : mot de passe avec « / » -> "failed to resolve
    # host" ; avec « @ » ou « % » ou une espace -> connexion refusée).
    return (
        f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{endpoint.host}:{endpoint.port}/{quote(db, safe='')}"
    )


#: URL explicite de la base (profil autonome `apex.standalone` : SQLite embarquée ; ou toute URL fournie
#: par l'opérateur). Prioritaire sur `config.services.postgres`, partout — un seul point de résolution.
DATABASE_URL_ENV = "APEX_DATABASE_URL"


def resolve_database_url(config: ApexConfig, *, environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else dict(os.environ)
    explicit = (env.get(DATABASE_URL_ENV) or "").strip()
    return explicit or database_url_from_config(config, environ=env)


def is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def build_engine(url: str, *, echo: bool = False) -> Engine:
    """Crée un engine SQLAlchemy pour l'URL donnée.

    Ne se connecte pas immédiatement — SQLAlchemy est paresseux par défaut ;
    la première connexion réelle a lieu au premier usage de la session.

    SQLite (profil autonome) : connexions partagées entre les fils de l'API et de la boucle
    (`check_same_thread=False`), attente d'un verrou au lieu d'un échec immédiat (30 s), journal WAL
    (lecteurs non bloqués par un écrivain) et clés étrangères appliquées.
    """
    if not is_sqlite_url(url):
        return create_engine(url, echo=echo, future=True)
    from sqlalchemy import event

    engine = create_engine(url, echo=echo, future=True,
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Une transaction par bloc : commit si tout va bien, rollback sinon.

    Aucun appelant ne doit gérer commit/rollback lui-même — c'est la source
    classique de connexions laissées à moitié ouvertes.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
