"""Support partagé pour les tests PostgreSQL réels (migrations, sécurité).

Rien ici n'est SIMULATED : `postgres_testable()` vérifie une connectivité
réelle plutôt que de supposer. Si elle retourne False, les tests qui en
dépendent doivent être sautés explicitement (`unittest.skipUnless`), jamais
remplacés par une assertion qui passerait sans avoir rien vérifié.

Toutes les valeurs de connexion viennent de l'environnement — aucune n'est
en dur ici, pour que ce module fonctionne aussi bien contre l'instance
PostgreSQL ad hoc de ce conteneur que contre `docker-compose.yml` sur WSL2.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config

#: Mots de passe des rôles applicatifs créés par la migration initiale.
ROLE_PASSWORD_VARS = (
    "APEX_DB_CORE_PASSWORD",
    "APEX_DB_AGENT_PASSWORD",
    "APEX_DB_EVAL_PASSWORD",
    "APEX_DB_AUDIT_PASSWORD",
    "APEX_DB_READONLY_PASSWORD",
)

REQUIRED_ENV = ("POSTGRES_USER", "POSTGRES_PASSWORD") + ROLE_PASSWORD_VARS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DB_NAME = os.environ.get("APEX_PHASE2_TEST_DB", "apex_phase2_test")


def _pg_host_port() -> tuple[str, int]:
    return (
        os.environ.get("APEX_TEST_PG_HOST", "127.0.0.1"),
        int(os.environ.get("APEX_TEST_PG_PORT", "5432")),
    )


def _tcp_reachable() -> bool:
    host, port = _pg_host_port()
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def postgres_testable() -> bool:
    """True seulement si PostgreSQL est réellement joignable ET que toutes
    les variables d'environnement nécessaires sont définies. Jamais supposé."""
    if not all(os.environ.get(v) for v in REQUIRED_ENV):
        return False
    return _tcp_reachable()


def _admin_connection():
    import contextlib

    import psycopg

    host, port = _pg_host_port()
    conn = psycopg.connect(
        host=host, port=port, dbname="postgres",
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
    )
    return contextlib.closing(conn)


def recreate_test_database() -> None:
    """Base de test jetable : supprimée puis recréée, jamais réutilisée telle quelle."""
    with _admin_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB_NAME,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')


def drop_test_database() -> None:
    with _admin_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (TEST_DB_NAME,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')


def drop_application_roles() -> None:
    """Les rôles sont globaux au cluster (pas à la base) : nettoyage indépendant du DROP DATABASE."""
    from apex.db.engine import APPLICATION_ROLES

    with _admin_connection() as conn, conn.cursor() as cur:
        for role in APPLICATION_ROLES:
            cur.execute(f"DROP ROLE IF EXISTS {role}")


@contextmanager
def _test_db_env() -> Iterator[None]:
    original = os.environ.get("POSTGRES_DB")
    os.environ["POSTGRES_DB"] = TEST_DB_NAME
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = original


def _alembic_config() -> Config:
    cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(REPO_ROOT, "migrations"))
    return cfg


def run_upgrade(revision: str = "head") -> None:
    with _test_db_env():
        command.upgrade(_alembic_config(), revision)


def run_downgrade(revision: str = "base") -> None:
    with _test_db_env():
        command.downgrade(_alembic_config(), revision)


def role_connection(role: str):
    """Connexion psycopg AVEC le rôle applicatif — pas le superutilisateur.

    C'est la seule façon de prouver qu'une politique de rôle est réellement
    appliquée par le SGBD : une session superutilisateur contournerait tout
    GRANT/REVOKE et le test ne prouverait rien.
    """
    import contextlib

    import psycopg

    host, port = _pg_host_port()
    var = f"APEX_DB_{role.removeprefix('apex_').upper()}_PASSWORD"
    conn = psycopg.connect(
        host=host, port=port, dbname=TEST_DB_NAME,
        user=role, password=os.environ[var], autocommit=True,
    )
    return contextlib.closing(conn)


def threaded_sqlite_session_factory():
    """Base SQLite FICHIER (répertoire temporaire supprimé à la fin du processus), UNE connexion par fil.

    À utiliser dès qu'un fil d'arrière-plan (boucle d'apprentissage, TaskRun, Scheduler) et le fil du test
    touchent la base en même temps : avec `sqlite://` + `StaticPool`, ils partageraient UNE connexion, dont
    l'usage concurrent mélange les transactions (lectures fantômes) et peut faire planter `sqlite3`
    (segfault constaté dans `tests/test_learning_orchestrator.py`)."""
    import atexit
    import shutil
    import tempfile

    from sqlalchemy import create_engine

    from apex.db.engine import build_session_factory
    from apex.db.models import Base

    directory = tempfile.mkdtemp(prefix="apex-test-db-")
    atexit.register(shutil.rmtree, directory, True)
    engine = create_engine(
        f"sqlite:///{directory}/apex.db", connect_args={"check_same_thread": False, "timeout": 30}, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)
