"""Tests de `apex.doctor.checks.services.check_postgres_auth` — écart réel
diagnostiqué en usage physique : `service.postgres` (`tcp_open` seul) passait
`PASS` alors que le rôle applicatif avait un mot de passe désynchronisé de
`POSTGRES_PASSWORD` (.env), et chaque requête SQL réelle de l'API échouait en
silence côté diagnostic. `psycopg.connect` est mocké — jamais un vrai
Postgres dans ce test, même discipline que `FakeProbe` pour le reste du
doctor."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import psycopg

from apex.config.loader import parse_config
from apex.doctor.check import CheckContext, Severity
from apex.doctor.checks import services
from apex.util.probe import FakeProbe
from tests.test_config import config as base_config


def context() -> CheckContext:
    return CheckContext(probe=FakeProbe(), config=parse_config(base_config()), timeout_s=1.0)


class TestCheckPostgresAuth(unittest.TestCase):
    def test_a_real_authenticated_connection_succeeding_is_pass(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {"POSTGRES_USER": "apex", "POSTGRES_PASSWORD": "correct-pw", "POSTGRES_DB": "apex"},
            ),
            patch("psycopg.connect") as fake_connect,
        ):
            fake_connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = (
                MagicMock()
            )
            r = services.check_postgres_auth(context())
        self.assertIs(r.severity, Severity.PASS)
        self.assertEqual(r.name, "service.postgres.auth")

    def test_b_password_mismatch_is_fail_with_clear_remediation(self) -> None:
        """Le scénario réel diagnostiqué : `FATAL: password authentication
        failed for user "apex"` — distingué de toute autre erreur de
        connexion, avec une remédiation qui nomme la cause connue (volume
        Postgres jamais resynchronisé après sa première initialisation)."""
        with (
            patch.dict(
                "os.environ",
                {"POSTGRES_USER": "apex", "POSTGRES_PASSWORD": "stale-pw", "POSTGRES_DB": "apex"},
            ),
            patch(
                "psycopg.connect",
                side_effect=psycopg.OperationalError(
                    'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
                    'FATAL:  password authentication failed for user "apex"'
                ),
            ),
        ):
            r = services.check_postgres_auth(context())
        self.assertIs(r.severity, Severity.FAIL)
        self.assertIn("mot de passe", r.message)
        self.assertIn("ALTER ROLE", r.remediation)

    def test_c_other_connection_failure_is_warn_not_fail(self) -> None:
        """Une erreur de connexion qui n'est PAS une authentification
        (Postgres qui redémarre, par exemple) ne doit jamais être confondue
        avec le cas mot de passe désynchronisé — `WARN`, pas `FAIL`, et
        aucune remédiation `ALTER ROLE` trompeuse."""
        with (
            patch.dict(
                "os.environ",
                {"POSTGRES_USER": "apex", "POSTGRES_PASSWORD": "any-pw", "POSTGRES_DB": "apex"},
            ),
            patch(
                "psycopg.connect",
                side_effect=psycopg.OperationalError("the database system is starting up"),
            ),
        ):
            r = services.check_postgres_auth(context())
        self.assertIs(r.severity, Severity.WARN)
        self.assertNotIn("ALTER ROLE", r.remediation)

    def test_d_missing_postgres_password_env_is_skip_never_crashes(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("POSTGRES_PASSWORD", None)
            r = services.check_postgres_auth(context())
        self.assertIs(r.severity, Severity.SKIP)

    def test_e_missing_db_extras_is_skip(self) -> None:
        """Le doctor doit rester utilisable sur une installation sans les
        extras `db` (sqlalchemy/psycopg) — jamais un crash `ImportError`."""
        with (
            patch.dict("os.environ", {"POSTGRES_PASSWORD": "x"}),
            patch.dict("sys.modules", {"psycopg": None}),
        ):
            r = services.check_postgres_auth(context())
        self.assertIs(r.severity, Severity.SKIP)

    def test_f_registered_in_the_full_checks_tuple(self) -> None:
        names = [name for name, _ in services.CHECKS]
        self.assertIn("service.postgres.auth", names)


if __name__ == "__main__":
    unittest.main()
