"""Tests de WeaknessDetectorService — intégration réelle SQLite : tâches et
EvalRun réellement persistés, rassemblés par spécialité, classifiés."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.evalrunner.repository import EvalRunRepository
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.sandbox.model import ExecutionStatus
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.weakness.model import WeaknessCategory
from apex.weakness.service import WeaknessDetectorService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def persist_run(repo: EvalRunRepository, *, task_id: str, clone_id: str, verdict: OracleVerdict) -> EvalRun:
    run = EvalRun.new(
        task_id=task_id, clone_id=clone_id, method=EvalMethod.PRACTICE, oracle_verdict=verdict,
        oracle_detail=None, sandbox_status=ExecutionStatus.SUCCESS, exit_code=0,
        elapsed_s=0.1, facts={}, created_by="core",
    )
    repo.create(run)
    return run


class TestDetectForSpecialty(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.tasks = TaskBankService(self.factory)
        self.eval_repo = EvalRunRepository(self.factory)
        self.service = WeaknessDetectorService(self.factory)

    def test_no_tasks_for_specialty_is_insufficient_data(self) -> None:
        report = self.service.detect_for_specialty("research")
        self.assertEqual(report.category, WeaknessCategory.INSUFFICIENT_DATA)

    def test_aggregates_runs_across_multiple_tasks_of_the_same_specialty(self) -> None:
        t1 = self.tasks.create(
            split=TaskSplit.TRAIN, specialty="research", title="t1", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        t2 = self.tasks.create(
            split=TaskSplit.TRAIN, specialty="research", title="t2", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        persist_run(self.eval_repo, task_id=t1.id, clone_id="c1", verdict=OracleVerdict.FAIL)
        persist_run(self.eval_repo, task_id=t2.id, clone_id="c1", verdict=OracleVerdict.FAIL)
        report = self.service.detect_for_specialty("research")
        self.assertEqual(report.fail_count, 2)
        self.assertEqual(report.category, WeaknessCategory.REPEATED_WEAKNESS)

    def test_excludes_tasks_of_other_specialties(self) -> None:
        research_task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        persist_run(self.eval_repo, task_id=research_task.id, clone_id="c1", verdict=OracleVerdict.PASS)
        report = self.service.detect_for_specialty("code-intelligence")
        self.assertEqual(report.category, WeaknessCategory.INSUFFICIENT_DATA)

    def test_clone_id_filter_isolates_a_single_clone(self) -> None:
        task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        persist_run(self.eval_repo, task_id=task.id, clone_id="c1", verdict=OracleVerdict.FAIL)
        persist_run(self.eval_repo, task_id=task.id, clone_id="c2", verdict=OracleVerdict.PASS)
        report_c1 = self.service.detect_for_specialty("research", clone_id="c1")
        report_c2 = self.service.detect_for_specialty("research", clone_id="c2")
        self.assertEqual(report_c1.category, WeaknessCategory.ISOLATED_FAILURE)
        self.assertEqual(report_c2.category, WeaknessCategory.NO_WEAKNESS)

    def test_heldout_tasks_are_included_same_as_train(self) -> None:
        """WeaknessDetector n'est PAS un exécuteur d'évaluation — il lit des
        résultats déjà persistés, jamais le contenu d'une tâche HELDOUT lui
        n'exécute rien. Aucune restriction de split ici : le split protège
        l'accès à `tasks` en amont (apex_eval), pas la lecture par ce module."""
        heldout_task = self.tasks.create(
            split=TaskSplit.HELDOUT, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        persist_run(self.eval_repo, task_id=heldout_task.id, clone_id="c1", verdict=OracleVerdict.FAIL)
        report = self.service.detect_for_specialty("research")
        self.assertEqual(report.fail_count, 1)


if __name__ == "__main__":
    unittest.main()
