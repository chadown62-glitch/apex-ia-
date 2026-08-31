"""Intégration réelle : TaskGenerator -> TaskBankService -> EvalRunnerService
-> vraie sandbox namespaced. Preuve de bout en bout, pas seulement au niveau
génération pure (tests/test_taskgen_deterministic.py) : un script de niveau
3 (qui ÉCHOUE si l'isolation réseau est violée) doit RÉUSSIR quand il tourne
réellement sous isolation namespaced — la même preuve « depuis l'intérieur »
déjà établie par tests/test_missions_execution.py pour le diagnostic
d'origine.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.taskgen.deterministic import DeterministicTaskGenerator
from apex.taskgen.model import GenerationMode
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


class TestGeneratedTaskRunsForRealUnderIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.tasks = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.generator = DeterministicTaskGenerator()
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )

    def test_level_3_generated_task_passes_under_real_sandbox_isolation(self) -> None:
        """Hors sandbox (tests/test_taskgen_deterministic.py), ce même
        niveau 3 ÉCHOUE parce que le réseau est réellement joignable. Ici,
        sous isolation namespaced réelle, il doit RÉUSSIR — preuve que
        l'isolation fonctionne, depuis l'intérieur du bac à sable."""
        spec = self.generator.generate(mode=GenerationMode.INCREASING_DIFFICULTY, specialty="research", level=3)
        task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec.specialty, title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertEqual(run.oracle_verdict, OracleVerdict.PASS)

    def test_targeted_weakness_task_is_persisted_and_traceable_to_its_generation(self) -> None:
        from apex.weakness.model import WeaknessCategory, WeaknessReport

        report = WeaknessReport(
            category=WeaknessCategory.STATISTICALLY_CREDIBLE_WEAKNESS, pass_count=1, fail_count=4,
            error_count=0, timeout_count=0, unknown_count=0, failure_rate=0.8,
            evidence_run_ids=("r1", "r2"), reason="test",
        )
        spec = self.generator.generate(
            mode=GenerationMode.TARGETED_WEAKNESS, specialty="research", weakness_report=report,
        )
        task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec.specialty, title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertEqual(run.oracle_verdict, OracleVerdict.PASS)
        self.assertEqual(run.task_id, task.id)

    def test_regression_task_reproduces_and_passes_the_original(self) -> None:
        spec_normal = self.generator.generate(mode=GenerationMode.NORMAL, specialty="research")
        base_task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec_normal.specialty, title=spec_normal.title,
            description=spec_normal.description, command=spec_normal.command, created_by=Subject.HUMAN,
        )
        spec_regression = self.generator.generate(
            mode=GenerationMode.REGRESSION, specialty="research", base_task=base_task,
        )
        self.assertEqual(spec_regression.command, base_task.command)
        regression_task = self.tasks.create(
            split=TaskSplit.TRAIN, specialty=spec_regression.specialty, title=spec_regression.title,
            description=spec_regression.description, command=spec_regression.command, created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=regression_task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertEqual(run.oracle_verdict, OracleVerdict.PASS)


if __name__ == "__main__":
    unittest.main()
