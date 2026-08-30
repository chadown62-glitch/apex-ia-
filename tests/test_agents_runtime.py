"""Tests d'AgentRuntime — cycle de vie complet, SQLite en mémoire.

Comme documenté dans apex/agents/runtime.py : aucune isolation OS réelle
n'est testée ici (SandboxProvider n'existe pas). Ce qui est réellement
vérifié : la machine à états, l'application de PolicyEngine, la persistance
et la journalisation (events/audit_log), le comportement de retry/échec.

Un seul type de clone est exercé de bout en bout (RESEARCH), conformément à
la consigne — le mécanisme lui-même est générique (aucune branche par type).
"""

from __future__ import annotations

import time
import unittest
import unittest.mock

from sqlalchemy import create_engine

from apex.agents.model import CloneState
from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import AgentRepository, AuditLogRepository, EventRepository
from apex.errors import PolicyDenied
from apex.policy.engine import PolicyEngine
from apex.policy.model import Subject


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local-gpu", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


class TestSpawn(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)

    def test_spawn_creates_a_ready_clone(self) -> None:
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc", spawned_by=Subject.CORE,
        )
        self.assertEqual(clone.state, CloneState.READY)

    def test_spawn_persists_the_clone(self) -> None:
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc", spawned_by=Subject.HUMAN,
        )
        with session_scope(self.factory) as session:
            row = AgentRepository(session).get(clone.clone_id)
            assert row is not None
            self.assertEqual(row.state, "ready")
            self.assertEqual(row.type, "research")
            self.assertEqual(row.spawned_by, "human")

    def test_spawn_writes_a_clone_spawned_event(self) -> None:
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc", spawned_by=Subject.CORE,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
        kinds = [e.context.get("kind") for e in events if e.context.get("clone_id") == clone.clone_id]
        self.assertIn("clone.spawned", kinds)

    def test_a_clone_cannot_spawn_another_clone(self) -> None:
        """Seul CORE/HUMAN a SPAWN_CLONE — appliqué par PolicyEngine, pas ici."""
        with self.assertRaises(PolicyDenied):
            self.runtime.spawn(
                Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc",
                spawned_by=Subject.DEVELOPER,
            )

    def test_empty_genome_ref_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="", spawned_by=Subject.CORE)

    def test_core_or_human_cannot_be_a_clone_type(self) -> None:
        with self.assertRaises(ValueError):
            self.runtime.spawn(Subject.CORE, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)

    def test_pool_without_capabilities_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.runtime.spawn(
                Subject.RESEARCH, pool=a_pool(capabilities=()), genome_ref="x", spawned_by=Subject.CORE,
            )


class TestRun(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc", spawned_by=Subject.CORE,
        )

    def test_successful_run_terminates_the_clone(self) -> None:
        result = self.runtime.run(self.clone, lambda: "hypothèse trouvée")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "hypothèse trouvée")
        self.assertEqual(self.clone.state, CloneState.TERMINATED)
        self.assertGreaterEqual(result.elapsed_s, 0.0)

    def test_successful_run_writes_audit_log(self) -> None:
        self.runtime.run(self.clone, lambda: None)
        with session_scope(self.factory) as session:
            entries = AuditLogRepository(session).recent()
        targets = [e.target for e in entries]
        self.assertIn(self.clone.clone_id, targets)

    def test_run_requires_ready_state(self) -> None:
        self.runtime.run(self.clone, lambda: None)  # -> TERMINATED
        with self.assertRaises(ValueError):
            self.runtime.run(self.clone, lambda: None)

    def test_run_persists_terminated_state(self) -> None:
        self.runtime.run(self.clone, lambda: None)
        with session_scope(self.factory) as session:
            row = AgentRepository(session).get(self.clone.clone_id)
            assert row is not None
            self.assertEqual(row.state, "terminated")
            self.assertIsNotNone(row.terminated_at)

    def test_exception_in_work_crashes_then_retries(self) -> None:
        def boom():
            raise RuntimeError("échec délibéré")

        result = self.runtime.run(self.clone, boom)
        self.assertFalse(result.success)
        self.assertTrue(result.retried)
        self.assertEqual(self.clone.state, CloneState.READY)
        self.assertEqual(self.clone.retries_used, 1)
        self.assertIn("échec délibéré", self.clone.last_error)

    def test_exhausting_retries_ends_in_failed(self) -> None:
        def boom():
            raise RuntimeError("toujours en échec")

        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE, max_retries=1,
        )
        self.runtime.run(clone, boom)  # 1er échec -> retry (retries_used=1)
        result = self.runtime.run(clone, boom)  # 2e échec -> plus de retry
        self.assertFalse(result.success)
        self.assertFalse(result.retried)
        self.assertEqual(clone.state, CloneState.FAILED)
        self.assertTrue(clone.state.is_terminal)

    def test_timeout_is_detected_and_treated_as_a_crash(self) -> None:
        result = self.runtime.run(self.clone, lambda: time.sleep(2), timeout_s=0.05)
        self.assertFalse(result.success)
        self.assertIn("délai dépassé", result.error)


class TestTerminate(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc", spawned_by=Subject.CORE,
        )

    def test_human_can_terminate_a_clone(self) -> None:
        self.runtime.terminate(self.clone, requested_by=Subject.HUMAN, reason="mission annulée")
        self.assertEqual(self.clone.state, CloneState.TERMINATED)

    def test_a_clone_cannot_terminate_itself_or_another(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.runtime.terminate(self.clone, requested_by=Subject.RESEARCH, reason="x")

    def test_terminate_writes_audit_log_with_reason(self) -> None:
        self.runtime.terminate(self.clone, requested_by=Subject.CORE, reason="budget épuisé")
        with session_scope(self.factory) as session:
            entries = AuditLogRepository(session).recent()
        matching = [e for e in entries if e.target == self.clone.clone_id]
        self.assertTrue(any(e.details.get("reason") == "budget épuisé" for e in matching))


class TestExecuteInSandbox(unittest.TestCase):
    """Intégration réelle : LinuxNamespaceSandbox, vrai noyau, pas de mock —
    voir tests/test_sandbox_security.py pour les propriétés d'isolation
    elles-mêmes ; ici on vérifie que AgentRuntime orchestre correctement
    autour (policy, transitions de CloneState, événements)."""

    def setUp(self) -> None:
        from apex.sandbox import ResourceLimits, SandboxSpec, get_default_provider

        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:abc", spawned_by=Subject.CORE,
        )
        self.sandbox = get_default_provider()
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        self.spec = SandboxSpec(
            mission_id="mission-1", clone_id=self.clone.clone_id,
            command=("python3", "-c", "print('ok')"), limits=limits,
        )

    def test_core_can_execute_in_sandbox(self) -> None:
        from apex.sandbox import ExecutionStatus

        result = self.runtime.execute_in_sandbox(self.clone, self.sandbox, self.spec, requested_by=Subject.CORE)
        self.assertIs(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.clone.state, CloneState.TERMINATED)

    def test_a_clone_cannot_execute_itself_in_a_sandbox(self) -> None:
        """Un clone ne doit jamais pouvoir déclencher sa propre exécution —
        contournement direct de CORE -> POLICY -> AGENT RUNTIME."""
        with self.assertRaises(PolicyDenied):
            self.runtime.execute_in_sandbox(self.clone, self.sandbox, self.spec, requested_by=Subject.RESEARCH)
        self.assertEqual(self.clone.state, CloneState.READY)  # aucune transition n'a eu lieu

    def test_human_cannot_execute_directly_either(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.runtime.execute_in_sandbox(self.clone, self.sandbox, self.spec, requested_by=Subject.HUMAN)

    def test_requires_ready_state(self) -> None:
        self.runtime.execute_in_sandbox(self.clone, self.sandbox, self.spec)  # -> TERMINATED
        with self.assertRaises(ValueError):
            self.runtime.execute_in_sandbox(self.clone, self.sandbox, self.spec)

    def test_a_failing_command_crashes_the_clone_to_failed(self) -> None:
        from apex.sandbox import ResourceLimits, SandboxSpec

        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        failing_spec = SandboxSpec(
            mission_id="mission-1", clone_id=self.clone.clone_id,
            command=("bash", "-c", "exit 1"), limits=limits,
        )
        self.runtime.execute_in_sandbox(self.clone, self.sandbox, failing_spec)
        self.assertEqual(self.clone.state, CloneState.FAILED)
        self.assertTrue(self.clone.state.is_terminal)

    def test_sandbox_events_are_recorded(self) -> None:
        self.runtime.execute_in_sandbox(self.clone, self.sandbox, self.spec)
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent()
        kinds = [e.msg for e in events if e.context.get("clone_id") == self.clone.clone_id]
        self.assertIn("sandbox.created", kinds)
        self.assertIn("sandbox.started", kinds)
        self.assertIn("sandbox.completed", kinds)

    def test_unavailable_isolation_is_never_silently_accepted(self) -> None:
        from apex.errors import IsolationUnavailable
        from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
        from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport

        fake_report = SandboxCapabilityReport(
            level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
        )
        unavailable_sandbox = LinuxNamespaceSandbox(capability_report=fake_report)
        with self.assertRaises(IsolationUnavailable):
            self.runtime.execute_in_sandbox(self.clone, unavailable_sandbox, self.spec)
        self.assertEqual(self.clone.state, CloneState.FAILED)


class TestImportableWithoutSqlalchemy(unittest.TestCase):
    """Garde de non-régression : le registre déclare module="apex.agents.runtime"
    (module_importable(), ADR-0007). Importer un sous-module importe d'abord
    apex/agents/__init__.py — si celui-ci (ou un module qu'il réexporte)
    importe SQLAlchemy au niveau module, la garantie serait fausse. Même
    piège que celui déjà rencontré avec apex.db.event_sink en Phase 2."""

    def test_module_imports_with_sqlalchemy_hidden(self) -> None:
        import sys

        blocked = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "sqlalchemy"
            or name.startswith("sqlalchemy.")
            or name.startswith("apex.agents")
            or name.startswith("apex.db.")
        }
        try:
            with unittest.mock.patch.dict(sys.modules, {"sqlalchemy": None}):
                import apex.agents.runtime as fresh_runtime

                self.assertTrue(hasattr(fresh_runtime, "AgentRuntime"))
        finally:
            for name in list(sys.modules):
                if name.startswith("apex.agents") or name.startswith("apex.db."):
                    del sys.modules[name]
            sys.modules.update(blocked)


if __name__ == "__main__":
    unittest.main()
