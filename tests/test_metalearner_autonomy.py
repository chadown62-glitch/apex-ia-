"""Tests de `apex.metalearner.autonomy.AutonomousLearningService` — passe
« Autonomous Learning Trigger + Scheduler/CLI Execution Path ».

SQLite en mémoire (`poolclass=StaticPool`, `check_same_thread=False`) — le
service exécute réellement chaque cycle sur un thread démon distinct (voir
`AutonomousLearningService.start_cycle`), et SQLite `:memory:` isole une
base PAR CONNEXION par défaut : sans `StaticPool`, le thread du cycle
verrait une base vide (« no such table »), contrairement à PostgreSQL/un
fichier SQLite réel où toutes les connexions partagent la même base. Même
sandbox namespaced RÉELLE qu'`apex.metalearner.cycle` (aucune simulation) —
seul le choix du moteur SQL change.
"""

from __future__ import annotations

import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import CycleAlreadyRunning
from apex.genome.service import GenomeStoreService
from apex.metalearner.autonomy import AutonomousLearningService, CycleState
from apex.policy.model import Subject


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.SANDBOX,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


class FakeConfig:
    def __init__(self, pools: dict[str, ComputePool]) -> None:
        self.pools = pools


def _wait_terminal(service: AutonomousLearningService, *, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    status = service.status()
    while status.state in (CycleState.RUNNING, CycleState.CANCEL_REQUESTED) and time.monotonic() < deadline:
        time.sleep(0.05)
        status = service.status()
    return status


def _promote_a_genome(factory) -> None:
    genome = GenomeStoreService(factory)
    genome.propose(content={"x": 1}, created_by=Subject.HUMAN)
    proposed = genome.list_all()[0]
    genome.promote(proposed.id, requested_by=Subject.HUMAN)


class TestStartCycleEndToEnd(unittest.TestCase):
    """Preuve centrale : `start_cycle` déclenche réellement
    `run_experiment_cycle` (sandbox réelle, jamais simulé), auto-provisionne
    un clone `Subject.LEARNING`, et rend la main immédiatement (`RUNNING`)
    avant que le thread de fond n'ait terminé."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        _promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()})
        self.service = AutonomousLearningService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_start_cycle_returns_running_immediately(self) -> None:
        status = self.service.start_cycle(specialty="research", current_level=1)
        self.assertEqual(status.state, CycleState.RUNNING)
        self.assertIsNotNone(status.cycle_id)
        _wait_terminal(self.service)

    def test_cycle_completes_with_a_real_eval_run_and_task(self) -> None:
        self.service.start_cycle(specialty="research", current_level=1)
        final = _wait_terminal(self.service)
        self.assertEqual(final.state, CycleState.COMPLETED)
        self.assertIsNotNone(final.eval_run_id)
        self.assertIsNotNone(final.task_id)
        self.assertEqual(final.oracle_verdict, "pass")

    def test_reuses_an_explicitly_provided_clone_instead_of_spawning(self) -> None:
        clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:explicit", spawned_by=Subject.CORE,
        )
        self.service.start_cycle(specialty="research", current_level=1, clone_id=clone.clone_id)
        final = _wait_terminal(self.service)
        self.assertEqual(final.state, CycleState.COMPLETED)

    def test_auto_spawned_clone_uses_the_learning_subject(self) -> None:
        """Vérifie directement le type de sujet du clone auto-provisionné —
        `Subject.LEARNING`, jamais `Subject.RESEARCH` (réservé aux appels
        humains directs via `POST /experiment-cycles`)."""
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository, EvalRunRepository

        self.service.start_cycle(specialty="research", current_level=1)
        final = _wait_terminal(self.service)
        self.assertEqual(final.state, CycleState.COMPLETED)
        with session_scope(self.factory) as session:
            eval_run = EvalRunRepository(session).get(final.eval_run_id)
            clone_row = AgentRepository(session).get(eval_run.clone_id)
        self.assertEqual(clone_row.type, Subject.LEARNING.value)


class TestDuplicateProtection(unittest.TestCase):
    """Un cycle `RUNNING` ne doit jamais être doublé — `CycleAlreadyRunning`,
    jamais un second thread silencieusement concurrent."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        _promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()})
        self.service = AutonomousLearningService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_second_start_while_running_is_rejected(self) -> None:
        self.service.start_cycle(specialty="research", current_level=1)
        with self.assertRaises(CycleAlreadyRunning):
            self.service.start_cycle(specialty="research", current_level=1)
        _wait_terminal(self.service)

    def test_a_new_cycle_can_start_once_the_previous_one_completed(self) -> None:
        self.service.start_cycle(specialty="research", current_level=1)
        first = _wait_terminal(self.service)
        self.assertEqual(first.state, CycleState.COMPLETED)
        second = self.service.start_cycle(specialty="research", current_level=1)
        self.assertEqual(second.state, CycleState.RUNNING)
        self.assertNotEqual(second.cycle_id, first.cycle_id)
        _wait_terminal(self.service)


class TestFailureHandling(unittest.TestCase):
    """Un cycle doit échouer PROPREMENT — jamais planter le processus,
    jamais rester bloqué `RUNNING` après une exception inattendue."""

    def test_no_promoted_genome_fails_cleanly_without_spawning(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        config = FakeConfig({"local": a_pool()})
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)

        service.start_cycle(specialty="research", current_level=1)
        final = _wait_terminal(service)
        self.assertEqual(final.state, CycleState.FAILED)
        self.assertIsNotNone(final.error)
        self.assertIn("GenomeVersion", final.error)

    def test_no_active_sandbox_capable_pool_fails_cleanly(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        no_sandbox_pool = a_pool(capabilities=(Capability.INFERENCE,))
        config = FakeConfig({"local": no_sandbox_pool})
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)

        service.start_cycle(specialty="research", current_level=1)
        final = _wait_terminal(service)
        self.assertEqual(final.state, CycleState.FAILED)
        self.assertIn("sandbox", final.error)

    def test_unknown_clone_id_fails_cleanly(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        config = FakeConfig({"local": a_pool()})
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)

        service.start_cycle(specialty="research", current_level=1, clone_id="does-not-exist")
        final = _wait_terminal(service)
        self.assertEqual(final.state, CycleState.FAILED)
        self.assertIsNotNone(final.error)

    def test_a_failed_cycle_never_leaves_the_service_stuck_running(self) -> None:
        """Une fois `FAILED`, un nouveau cycle doit pouvoir démarrer —
        jamais bloqué par un échec précédent."""
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        config = FakeConfig({"local": a_pool()})
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)

        service.start_cycle(specialty="research", current_level=1)
        first = _wait_terminal(service)
        self.assertEqual(first.state, CycleState.FAILED)

        _promote_a_genome(factory)
        second = service.start_cycle(specialty="research", current_level=1)
        self.assertEqual(second.state, CycleState.RUNNING)
        final = _wait_terminal(service)
        self.assertEqual(final.state, CycleState.COMPLETED)


class TestStatusAndHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        _promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()})
        self.service = AutonomousLearningService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_status_is_idle_before_any_cycle(self) -> None:
        self.assertEqual(self.service.status().state, CycleState.IDLE)

    def test_history_is_empty_before_any_cycle(self) -> None:
        self.assertEqual(self.service.history(), [])

    def test_history_reflects_completed_and_failed_cycles_durably(self) -> None:
        self.service.start_cycle(specialty="research", current_level=1)
        _wait_terminal(self.service)

        history = self.service.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].state, CycleState.COMPLETED)
        self.assertEqual(history[0].specialty, "research")
        self.assertIsNotNone(history[0].eval_run_id)

    def test_history_is_ordered_most_recent_first(self) -> None:
        self.service.start_cycle(specialty="research", current_level=1)
        first = _wait_terminal(self.service)
        self.service.start_cycle(specialty="research", current_level=1)
        second = _wait_terminal(self.service)

        history = self.service.history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].cycle_id, second.cycle_id)
        self.assertEqual(history[1].cycle_id, first.cycle_id)

    def test_history_respects_limit(self) -> None:
        for _ in range(3):
            self.service.start_cycle(specialty="research", current_level=1)
            _wait_terminal(self.service)
        self.assertEqual(len(self.service.history(limit=2)), 2)


class TestCancellation(unittest.TestCase):
    def test_cancel_is_a_noop_when_nothing_is_running(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        config = FakeConfig({"local": a_pool()})
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        status = service.cancel()
        self.assertEqual(status.state, CycleState.IDLE)

    def test_cancel_requests_real_cancellation_on_a_running_cycle(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = FakeConfig({"local": a_pool()})
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)

        service.start_cycle(specialty="research", current_level=1)
        status = service.cancel()
        self.assertIn(status.state, (CycleState.CANCEL_REQUESTED, CycleState.COMPLETED, CycleState.CANCELLED))
        final = _wait_terminal(service)
        self.assertIn(final.state, (CycleState.COMPLETED, CycleState.CANCELLED))


if __name__ == "__main__":
    unittest.main()
