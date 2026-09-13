"""Tests d'`apex.metalearner.experience` — persistance de l'interprétation
d'un `EvalRun` pour la boucle d'apprentissage adaptative (N+20). SQLite en
mémoire, même patron que `tests/test_evalrunner_generated_solution.py`."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.metalearner.experience import Experience, ExperienceRepository, build_experience
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.population_fitness import FitnessResult
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _a_passing_eval_run(**overrides) -> EvalRun:
    defaults = dict(
        id="run-1", task_id="task-1", method=EvalMethod.EXPERIMENTATION,
        oracle_verdict=OracleVerdict.PASS, oracle_detail=None, exit_code=0, elapsed_s=0.05,
        facts={"model_response": {"text": "def is_palindrome(s):\n    return s == s[::-1]\n", "truncated": False, "model_used": "fake-model", "finish_reason": "stop", "tokens_prompt": 10, "tokens_completion": 8}},
        created_by="core", clone_id="clone-1", sandbox_status=ExecutionStatus.SUCCESS, model_id="fake-model",
        started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return EvalRun(**defaults)


class TestBuildExperience(unittest.TestCase):
    def test_a_all_traceable_identifiers_are_carried_over(self) -> None:
        run = _a_passing_eval_run()
        fitness = FitnessResult(value=1.2, components={"quality": 1.0})
        experience = build_experience(
            run, fitness, strategy_id="g0-mut0", generation_number=0,
            generation_config={"generation": {"temperature": 0.7}}, created_by="core",
        )
        self.assertEqual(experience.task_id, run.task_id)
        self.assertEqual(experience.candidate_id, run.id)
        self.assertEqual(experience.eval_run_id, run.id)
        self.assertEqual(experience.model_id, run.model_id)
        self.assertEqual(experience.generation_number, 0)
        self.assertEqual(experience.fitness, 1.2)
        self.assertIs(experience.oracle_verdict, OracleVerdict.PASS)
        self.assertIs(experience.failure_category, FailureCategory.SUCCESS)
        self.assertIsNotNone(experience.solution_fingerprint)
        self.assertIsNone(experience.parent_experience_id)

    def test_b_fingerprint_is_a_hash_never_the_raw_text(self) -> None:
        run = _a_passing_eval_run()
        fitness = FitnessResult(value=1.0, components={})
        experience = build_experience(
            run, fitness, strategy_id="g0-mut0", generation_number=0, generation_config={}, created_by="core",
        )
        self.assertNotIn("def is_palindrome", experience.solution_fingerprint)
        self.assertEqual(len(experience.solution_fingerprint), 64)  # sha256 hexdigest

    def test_c_no_generation_yields_no_fingerprint(self) -> None:
        run = _a_passing_eval_run(facts={}, model_id=None, sandbox_status=None, oracle_verdict=OracleVerdict.ERROR)
        fitness = FitnessResult(value=-0.3, components={})
        experience = build_experience(
            run, fitness, strategy_id="g0-mut0", generation_number=0, generation_config={}, created_by="core",
        )
        self.assertIsNone(experience.solution_fingerprint)
        self.assertIs(experience.failure_category, FailureCategory.GENERATION_FAILURE)


class TestExperienceRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = ExperienceRepository(self.factory)

    def test_a_create_and_get_round_trip(self) -> None:
        run = _a_passing_eval_run()
        fitness = FitnessResult(value=1.2, components={})
        experience = build_experience(
            run, fitness, strategy_id="g0-mut0", generation_number=0,
            generation_config={"generation": {"temperature": 0.7}}, created_by="core",
        )
        self.repo.create(experience)
        reread = self.repo.get(experience.id)
        self.assertIsNotNone(reread)
        self.assertEqual(reread.id, experience.id)
        self.assertEqual(reread.fitness, 1.2)
        self.assertIs(reread.failure_category, FailureCategory.SUCCESS)
        self.assertEqual(reread.generation_config, {"generation": {"temperature": 0.7}})

    def test_b_list_by_task_and_generation_filters_correctly(self) -> None:
        for gen in (0, 0, 1):
            run = _a_passing_eval_run(id=f"run-{gen}-{id(object())}")
            fitness = FitnessResult(value=1.0, components={})
            experience = build_experience(
                run, fitness, strategy_id="s", generation_number=gen, generation_config={}, created_by="core",
            )
            self.repo.create(experience)

        gen0 = self.repo.list_by_task_and_generation("task-1", 0)
        gen1 = self.repo.list_by_task_and_generation("task-1", 1)
        gen2 = self.repo.list_by_task_and_generation("task-1", 2)
        self.assertEqual(len(gen0), 2)
        self.assertEqual(len(gen1), 1)
        self.assertEqual(len(gen2), 0)

    def test_c_parent_lineage_is_persisted(self) -> None:
        parent_run = _a_passing_eval_run(id="run-parent")
        parent = build_experience(
            parent_run, FitnessResult(value=0.4, components={}), strategy_id="g0-mut0",
            generation_number=0, generation_config={}, created_by="core",
        )
        self.repo.create(parent)

        child_run = _a_passing_eval_run(id="run-child")
        child = build_experience(
            child_run, FitnessResult(value=0.9, components={}), strategy_id="g1-mut0",
            generation_number=1, generation_config={}, created_by="core",
            parent_experience_id=parent.id, parent_generation=0,
            mutation_description="évidence: incorrect_solution -> température augmentée",
            improvement_delta=0.5,
        )
        self.repo.create(child)

        reread = self.repo.get(child.id)
        self.assertEqual(reread.parent_experience_id, parent.id)
        self.assertEqual(reread.parent_generation, 0)
        self.assertAlmostEqual(reread.improvement_delta, 0.5)
        self.assertIn("température", reread.mutation_description)


if __name__ == "__main__":
    unittest.main()
