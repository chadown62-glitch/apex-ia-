"""Tests du point d'entrée réel de la boucle adaptative (`apex solve`,
`apex.metalearner.solution_loop_runner`) — avant ce point d'entrée, la
boucle n'avait aucun appelant hors tests.

Sandbox, EvalRunner, Experience et configuration RÉELS ; seule la base est
une SQLite en mémoire (même schéma) et le modèle un double scripté (aucun
Ollama joignable ici)."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sqlalchemy import create_engine

from apex.cli.main import main
from apex.config.loader import load_config
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.metalearner.solution_loop_runner import (
    SolutionLoopRequest,
    SolutionLoopUnavailable,
    initial_strategies,
    resolve_endpoint,
    resolve_local_pool,
    run_solution_loop,
)
from apex.models.model import GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.taskgen.solution_tasks import build_palindrome_task_spec
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService

_CORRECT = "def is_palindrome(s):\n    c = s.lower()\n    return c == c[::-1]\n"


class _Correct(ModelProvider):
    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="double", detail="double")

    def generate(self, request):
        return GenerationResult(
            text=_CORRECT,
            model_used="double",
            tokens_prompt=Measurement.real(1),
            tokens_completion=Measurement.real(1),
            finish_reason="stop",
            elapsed_s=0.0,
            facts={},
        )


def _factory_with_task():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    spec = build_palindrome_task_spec()
    extra = {}
    if getattr(spec, "expected_answer", None) is not None:
        extra["expected_answer"] = spec.expected_answer
    task = TaskBankService(factory).create(
        split=TaskSplit.TRAIN,
        specialty="research",
        title=spec.title,
        description=spec.description,
        command=spec.command,
        created_by=Subject.HUMAN,
        solution_harness=spec.solution_harness,
        **extra,
    )
    return factory, task


class TestResolution(unittest.TestCase):
    def test_coder_role_resolves_to_a_declared_endpoint(self) -> None:
        endpoint = resolve_endpoint(load_config(), "coder_main")
        self.assertTrue(endpoint.served_model)

    def test_unknown_role_is_refused_never_guessed(self) -> None:
        with self.assertRaises(SolutionLoopUnavailable):
            resolve_endpoint(load_config(), "no_such_role")

    def test_a_local_active_pool_is_used(self) -> None:
        self.assertEqual(resolve_local_pool(load_config()).location.value, "local")

    def test_initial_strategies_are_distinct_and_bounded(self) -> None:
        temps = [s.genome_content["generation"]["temperature"] for s in initial_strategies(0.05, 3)]
        self.assertEqual(len(set(temps)), 3)
        self.assertTrue(all(0.0 <= t <= 2.0 for t in temps))
        with self.assertRaises(ValueError):
            initial_strategies(0.7, 0)


class TestRunSolutionLoop(unittest.TestCase):
    def test_real_loop_runs_from_configuration_to_a_judged_solution(self) -> None:
        factory, task = _factory_with_task()
        result = run_solution_loop(
            factory,
            load_config(),
            SolutionLoopRequest(task_id=task.id, generations=2, candidates=1),
            provider=_Correct(),
        )
        self.assertIs(result.best_overall.eval_run.oracle_verdict, OracleVerdict.PASS)
        self.assertIsNotNone(result.best_overall.eval_run.sandbox_status)
        self.assertEqual(result.stop_reason, "target_fitness_reached")


class TestSolveCommand(unittest.TestCase):
    def test_cli_runs_the_loop_and_reports_json(self) -> None:
        factory, task = _factory_with_task()
        out = io.StringIO()
        with (
            patch("apex.api.dependencies.get_session_factory", return_value=factory),
            patch("apex.evalrunner.service.get_default_model_provider", return_value=_Correct()),
            redirect_stdout(out),
        ):
            code = main(["solve", task.id, "--generations", "1", "--candidates", "1", "--json"])
        self.assertEqual(code, 0)
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["best_verdict"], "pass")
        self.assertTrue(summary["experiment_ref"].startswith("adaptive-loop:"))

    def test_cli_reports_missing_task_as_an_error(self) -> None:
        factory, _ = _factory_with_task()
        with patch("apex.api.dependencies.get_session_factory", return_value=factory):
            self.assertEqual(main(["solve", "no-such-task", "--generations", "1"]), 1)


if __name__ == "__main__":
    unittest.main()
