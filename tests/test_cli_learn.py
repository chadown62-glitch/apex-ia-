"""Tests de `apex learn start|status|history|stop` — passe « Autonomous
Learning Trigger + Scheduler/CLI Execution Path ». La CLI appelle le même
`apex.metalearner.autonomy.AutonomousLearningService` que l'API (via
`apex.api.dependencies.get_learning_service`, réutilisé tel quel — jamais
une seconde implémentation), vérifié en exécutant réellement
`apex.cli.main.main([...])` en mémoire (jamais un sous-processus), même
patron que les autres commandes CLI existantes.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from apex.api.dependencies import reset_learning_service_for_tests
from apex.cli.main import main
from apex.policy.model import Subject
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


class TestLearnStartValidation(unittest.TestCase):
    """Validation d'entrée — ne touche jamais la DB, fonctionne donc sans
    PostgreSQL configuré."""

    def test_unknown_specialty_fails_cleanly_with_a_clear_message(self) -> None:
        code, out, err = _run(["learn", "start", "--specialty", "not-a-real-specialty"])
        self.assertEqual(code, 1)
        self.assertIn("inconnue", err)
        self.assertEqual(out, "")

    def test_unknown_method_fails_cleanly(self) -> None:
        code, out, err = _run(["learn", "start", "--specialty", "research", "--method", "not-a-real-method"])
        self.assertEqual(code, 1)
        self.assertIn("méthode", err)

    def test_unknown_split_fails_cleanly(self) -> None:
        code, out, err = _run(["learn", "start", "--specialty", "research", "--split", "not-a-real-split"])
        self.assertEqual(code, 1)
        self.assertIn("split", err)

    def test_specialty_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            _run(["learn", "start"])


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestLearnCommandsWithRealDatabase(unittest.TestCase):
    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        reset_learning_service_for_tests()
        self._promote_a_genome()

    def tearDown(self) -> None:
        reset_learning_service_for_tests()
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

    def test_status_before_any_cycle_is_idle(self) -> None:
        code, out, _ = _run(["learn", "status", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "idle")

    def test_history_before_any_cycle_is_empty(self) -> None:
        code, out, _ = _run(["learn", "history", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_start_blocks_until_the_real_cycle_completes_and_prints_the_result(self) -> None:
        """Preuve centrale : `start` attend réellement la fin du cycle
        (thread démon) avant de rendre la main, plutôt que de laisser un
        cycle inachevé derrière lui — voir la docstring de `_cmd_learn_start`."""
        code, out, _ = _run(["learn", "start", "--specialty", "research", "--current-level", "1", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "completed")
        self.assertIsNotNone(payload["eval_run_id"])
        self.assertIsNotNone(payload["task_id"])
        self.assertEqual(payload["oracle_verdict"], "pass")

    def test_completed_cycle_is_durably_visible_in_history_afterwards(self) -> None:
        _run(["learn", "start", "--specialty", "research", "--current-level", "1", "--json"])
        code, out, _ = _run(["learn", "history", "--json"])
        self.assertEqual(code, 0)
        history = json.loads(out)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["state"], "completed")
        self.assertEqual(history[0]["specialty"], "research")

    def test_stop_on_an_idle_service_is_a_clean_noop(self) -> None:
        code, out, _ = _run(["learn", "stop", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "idle")

    def test_second_process_never_sees_the_running_state_of_a_different_process(self) -> None:
        """Limite honnête et documentée : l'état `RUNNING` est en mémoire,
        par PROCESSUS — puisque `start` bloque déjà jusqu'à la fin dans SON
        propre processus, `status` appelé après (donc dans un état
        `reset_learning_service_for_tests()` simulant un nouveau processus)
        ne peut voir qu'`IDLE`, jamais un doublon fantôme."""
        _run(["learn", "start", "--specialty", "research", "--current-level", "1", "--json"])
        reset_learning_service_for_tests()  # simule un nouveau processus CLI
        code, out, _ = _run(["learn", "status", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "idle")


if __name__ == "__main__":
    unittest.main()
