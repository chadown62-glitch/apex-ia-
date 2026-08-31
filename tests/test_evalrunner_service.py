"""Tests d'EvalRunnerService — intégration réelle (SQLite en mémoire + vraie
sandbox namespaced, pas de mock de l'exécution). Même patron que
tests/test_missions_execution.py : ce qui est vérifié est la chaîne complète
TASK + CLONE -> AGENT RUNTIME -> SANDBOX -> ORACLE -> EVAL_RUN persisté,
contre un VRAI processus, sous VRAIE isolation namespaced — jamais simulée.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.sandbox.model import ExecutionStatus
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


class TestEvalRunFullChain(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.task_service = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )

    def test_a_real_passing_command_yields_pass(self) -> None:
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "import sys; sys.exit(0)"), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertEqual(run.sandbox_status, ExecutionStatus.SUCCESS)
        self.assertEqual(run.oracle_verdict, OracleVerdict.PASS)
        self.assertEqual(run.exit_code, 0)

    def test_a_real_run_carries_a_real_measured_memory_peak_in_facts(self) -> None:
        """`memory_peak_mb` (cgroups v1 `memory.max_usage_in_bytes`, réel)
        est copié dans `facts` — le sac JSON libre déjà prévu par le
        contrat Phase 5, aucune colonne ajoutée. `cpu_time_s` reste
        délibérément absent de `facts` : `cpu.stat` (v1) n'expose aucune
        valeur réelle à copier (voir `apex.costgain.estimator`)."""
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "import sys; sys.exit(0)"), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertIn("memory_peak_mb", run.facts)
        self.assertIsInstance(run.facts["memory_peak_mb"], float)
        self.assertGreater(run.facts["memory_peak_mb"], 0.0)
        self.assertNotIn("cpu_time_s", run.facts)

    def test_strategy_facts_are_recorded_under_a_dedicated_namespace(self) -> None:
        """Ajout minimal (Priorité 5, MetaLearner v2) : un appelant peut
        associer sa décision de stratégie à CE run, sous `facts["strategy"]`
        — jamais mélangé aux clés mesurées par la sandbox (`memory_peak_mb`,
        `cgroup_name`, ...), aucune collision possible."""
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "import sys; sys.exit(0)"), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(
            task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE,
            strategy_facts={"generation_mode": "targeted_weakness", "curriculum_level": 3},
        )
        self.assertEqual(run.facts["strategy"], {"generation_mode": "targeted_weakness", "curriculum_level": 3})
        self.assertIn("memory_peak_mb", run.facts)

    def test_strategy_facts_absent_by_default_leaves_facts_unchanged(self) -> None:
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "import sys; sys.exit(0)"), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertNotIn("strategy", run.facts)

    def test_a_real_failing_command_yields_fail_not_error(self) -> None:
        """Un code de sortie non nul RÉEL est un FAIL de tâche, jamais une
        ERROR d'infrastructure — les deux vocabulaires ne sont jamais fusionnés."""
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "import sys; sys.exit(7)"), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertEqual(run.sandbox_status, ExecutionStatus.FAILED)
        self.assertEqual(run.oracle_verdict, OracleVerdict.FAIL)
        self.assertEqual(run.exit_code, 7)

    def test_negative_result_is_persisted_identically_to_a_positive_one(self) -> None:
        """Aucun filtrage à l'écriture : un FAIL est retenu exactement comme
        un PASS — jamais effacé ni transformé."""
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("python3", "-c", "import sys; sys.exit(1)"), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        fetched = self.evalrunner.get(run.id)
        assert fetched is not None
        self.assertEqual(fetched.oracle_verdict, OracleVerdict.FAIL)

    def test_run_is_traceable_to_task_and_clone(self) -> None:
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.RESEARCH)
        self.assertEqual(run.task_id, task.id)
        self.assertEqual(run.clone_id, self.clone.clone_id)
        by_task = self.evalrunner.list_by_task(task.id)
        self.assertIn(run.id, [r.id for r in by_task])
        by_clone = self.evalrunner.list_by_clone(self.clone.clone_id)
        self.assertIn(run.id, [r.id for r in by_clone])

    def test_method_and_seed_and_experiment_ref_are_persisted(self) -> None:
        """Collecte pure du champ method (docs/COGNITIVE_ARCHITECTURE.md §5)
        + affordances seed/experiment_ref pour les phases futures."""
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(
            task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.SELF_EXPLANATION,
            seed=42, experiment_ref="exp-alpha",
        )
        fetched = self.evalrunner.get(run.id)
        assert fetched is not None
        self.assertEqual(fetched.method, EvalMethod.SELF_EXPLANATION)
        self.assertEqual(fetched.seed, 42)
        self.assertEqual(fetched.experiment_ref, "exp-alpha")

    def test_events_reconstruct_the_run(self) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        run = self.evalrunner.run(task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE)
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent(limit=200)
        kinds = [e.msg for e in events if e.context.get("eval_run_id") == run.id]
        self.assertIn("evalrun.completed", kinds)

    def test_clone_cannot_launch_its_own_evaluation(self) -> None:
        """EvalRunner ne contourne jamais PolicyEngine : EXECUTE_IN_SANDBOX
        reste CORE seul, appliqué par AgentRuntime, réutilisé tel quel."""
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        with self.assertRaises(PolicyDenied):
            self.evalrunner.run(
                task_id=task.id, clone_id=self.clone.clone_id, method=EvalMethod.PRACTICE,
                requested_by=Subject.RESEARCH,
            )


class TestEvalRunPreflightValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.task_service = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)

    def test_nonexistent_task_raises(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        with self.assertRaises(LookupError):
            self.evalrunner.run(task_id="does-not-exist", clone_id=clone.clone_id, method=EvalMethod.PRACTICE)

    def test_nonexistent_clone_raises(self) -> None:
        task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        with self.assertRaises(ValueError):
            self.evalrunner.run(task_id=task.id, clone_id="does-not-exist", method=EvalMethod.PRACTICE)


class TestEvalRunSandboxUnavailable(unittest.TestCase):
    def test_unavailable_sandbox_yields_error_verdict_not_a_crash(self) -> None:
        from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
        from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport

        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        task_service = TaskBankService(factory)
        clone = runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        task = task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title="t", description="d",
            command=("true",), created_by=Subject.HUMAN,
        )
        fake_report = SandboxCapabilityReport(
            level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
        )
        unavailable_sandbox = LinuxNamespaceSandbox(capability_report=fake_report)
        evalrunner = EvalRunnerService(factory, agent_runtime=runtime, sandbox=unavailable_sandbox)

        run = evalrunner.run(task_id=task.id, clone_id=clone.clone_id, method=EvalMethod.PRACTICE)
        self.assertEqual(run.oracle_verdict, OracleVerdict.ERROR)
        fetched = evalrunner.get(run.id)
        assert fetched is not None
        self.assertEqual(fetched.oracle_verdict, OracleVerdict.ERROR)


if __name__ == "__main__":
    unittest.main()
