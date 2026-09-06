"""Tests de `apex learning start|status|list|resume|cancel` — passe
« Autonomous Multi-Generation Learning Orchestrator ». La CLI appelle le
MÊME `apex.metalearner.learning_orchestrator.LearningOrchestrator` que
l'API (via `apex.api.dependencies.get_learning_orchestrator`, réutilisé tel
quel — jamais une seconde implémentation de la boucle), vérifié en
exécutant réellement `apex.cli.main.main([...])` en mémoire (jamais un
sous-processus), même patron que `tests/test_cli_learn.py`.

Contrairement à `apex learn start` (qui BLOQUE jusqu'à la fin d'un cycle
unique), `apex learning start` rend la main IMMÉDIATEMENT (`running`) — la
boucle multi-génération continue en tâche de fond dans CE processus de
test ; chaque test qui a besoin d'un état terminal interroge donc
`apex learning status` en boucle (même discipline que les tests directs de
`LearningOrchestrator`)."""

from __future__ import annotations

import io
import json
import os
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

from apex.policy.model import Subject
from apex.cli.main import main
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestLearningStartValidation(unittest.TestCase):
    """Validation d'entrée — ne touche jamais la DB, fonctionne donc sans
    PostgreSQL configuré."""

    def test_unknown_specialty_fails_cleanly_with_a_clear_message(self) -> None:
        code, out, err = _run(["learning", "start", "--specialty", "not-a-real-specialty", "--generations", "3"])
        self.assertEqual(code, 1)
        self.assertIn("inconnue", err)
        self.assertEqual(out, "")

    def test_specialty_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            _run(["learning", "start", "--generations", "3"])

    def test_generations_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            _run(["learning", "start", "--specialty", "research"])


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestLearningCommandsWithRealDatabase(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self._promote_a_genome()

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _session_factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def _promote_a_genome(self) -> None:
        from apex.genome.service import GenomeStoreService

        genome = GenomeStoreService(self._session_factory())
        genome.propose(content={"x": 1}, created_by=Subject.HUMAN)
        proposed = genome.list_all()[0]
        genome.promote(proposed.id, requested_by=Subject.HUMAN)

    def _wait_terminal(self, session_id: str, *, timeout_s: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout_s
        _, out, _ = _run(["learning", "status", session_id, "--json"])
        body = json.loads(out)
        while body["status"] in ("running", "cancel_requested") and time.monotonic() < deadline:
            time.sleep(0.05)
            _, out, _ = _run(["learning", "status", session_id, "--json"])
            body = json.loads(out)
        return body

    def test_list_before_any_session_is_empty(self) -> None:
        code, out, _ = _run(["learning", "list", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_start_returns_immediately_in_running_state(self) -> None:
        code, out, _ = _run(["learning", "start", "--specialty", "research", "--generations", "2", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["specialty"], "research")
        self._wait_terminal(payload["id"])

    def test_full_multi_generation_run_completes_and_is_visible_via_status(self) -> None:
        _, out, _ = _run(["learning", "start", "--specialty", "research", "--generations", "2", "--json"])
        session_id = json.loads(out)["id"]
        final = self._wait_terminal(session_id)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["stop_reason"], "max_generations_reached")
        self.assertEqual(final["generations_completed"], 2)
        self.assertIsNotNone(final["best_fitness"])

    def test_completed_session_is_durably_visible_in_list_afterwards(self) -> None:
        _, out, _ = _run(["learning", "start", "--specialty", "research", "--generations", "1", "--json"])
        session_id = json.loads(out)["id"]
        self._wait_terminal(session_id)
        code, out, _ = _run(["learning", "list", "--specialty", "research", "--json"])
        self.assertEqual(code, 0)
        ids = [s["id"] for s in json.loads(out)]
        self.assertIn(session_id, ids)

    def test_second_start_for_the_same_specialty_is_rejected(self) -> None:
        _, out, _ = _run(["learning", "start", "--specialty", "research", "--generations", "20", "--json"])
        session_id = json.loads(out)["id"]
        code, _, err = _run(["learning", "start", "--specialty", "research", "--generations", "20", "--json"])
        self.assertEqual(code, 13)
        self.assertIn("research", err)
        _run(["learning", "cancel", session_id])
        self._wait_terminal(session_id)

    def test_cancel_then_resume_round_trip(self) -> None:
        _, out, _ = _run(["learning", "start", "--specialty", "research", "--generations", "20", "--json"])
        session_id = json.loads(out)["id"]
        time.sleep(0.1)
        code, out, _ = _run(["learning", "cancel", session_id, "--json"])
        self.assertEqual(code, 0)
        cancelled = self._wait_terminal(session_id)
        self.assertEqual(cancelled["status"], "cancelled")

        code, out, _ = _run(["learning", "resume", session_id, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "running")
        self._wait_terminal(session_id, timeout_s=5.0)
        _run(["learning", "cancel", session_id])
        self._wait_terminal(session_id)

    def test_status_of_unknown_session_fails_cleanly(self) -> None:
        code, out, err = _run(["learning", "status", "not-a-real-session-id"])
        self.assertEqual(code, 1)
        self.assertIn("introuvable", err)


if __name__ == "__main__":
    unittest.main()
