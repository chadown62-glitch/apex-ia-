"""Tests de `apex.metalearner.autonomous_scheduler.AutonomousLearningScheduler`
— passe « Autonomous Learning Trigger + Scheduler/CLI Execution Path ».

Même sandbox namespaced RÉELLE que `test_metalearner_autonomy.py` — le
Scheduler ne fait qu'appeler `AutonomousLearningService.start_cycle` à
intervalle, jamais une seconde implémentation du cycle lui-même.
"""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.genome.service import GenomeStoreService
from apex.metalearner.autonomous_scheduler import AutonomousLearningScheduler
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


def _promote_a_genome(factory) -> None:
    genome = GenomeStoreService(factory)
    genome.propose(content={"x": 1}, created_by=Subject.HUMAN)
    proposed = genome.list_all()[0]
    genome.promote(proposed.id, requested_by=Subject.HUMAN)


def _fake_config(*, enabled: bool, interval_s: float = 0.2, pools=None):
    return SimpleNamespace(
        pools=pools or {"local": a_pool()},
        autonomous_learning=SimpleNamespace(
            enabled=enabled, interval_s=interval_s, specialty="research", current_level=1,
            method="practice", split="train",
        ),
    )


def _wait_terminal(service: AutonomousLearningService, *, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    status = service.status()
    while status.state in (CycleState.RUNNING, CycleState.CANCEL_REQUESTED) and time.monotonic() < deadline:
        time.sleep(0.05)
        status = service.status()
    return status


class TestDisabledByDefault(unittest.TestCase):
    """Exigence de sécurité explicite : `enabled=False` (défaut) ->
    `start()` ne démarre RÉELLEMENT aucun thread — installer/mettre à jour
    APEX ne doit jamais démarrer un apprentissage autonome par surprise."""

    def test_start_is_a_noop_when_disabled(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        config = _fake_config(enabled=False)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        scheduler.start()
        self.assertFalse(scheduler.is_running)
        self.assertEqual(service.status().state, CycleState.IDLE)
        scheduler.stop()  # idempotent, même si jamais démarré


class TestEnabledConfiguration(unittest.TestCase):
    def test_start_actually_launches_the_background_thread_when_enabled(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = _fake_config(enabled=True, interval_s=60)  # long intervalle : un seul tick pendant le test
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        scheduler.start()
        self.assertTrue(scheduler.is_running)
        # laisse le premier tick se déclencher et le cycle se terminer
        deadline = time.monotonic() + 10
        while service.status().state == CycleState.IDLE and time.monotonic() < deadline:
            time.sleep(0.05)
        _wait_terminal(service)
        scheduler.stop()
        self.assertFalse(scheduler.is_running)
        self.assertEqual(len(service.history()), 1)
        self.assertEqual(service.history()[0].state, CycleState.COMPLETED)


class TestScheduledTrigger(unittest.TestCase):
    def test_tick_once_starts_a_cycle_using_the_policy_defaults(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = _fake_config(enabled=True)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        started = scheduler.tick_once()
        self.assertTrue(started)
        final = _wait_terminal(service)
        self.assertEqual(final.state, CycleState.COMPLETED)
        self.assertEqual(final.specialty, "research")


class TestNoDuplicateConcurrentCycle(unittest.TestCase):
    def test_tick_once_is_ignored_while_a_cycle_is_already_running(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = _fake_config(enabled=True)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        service.start_cycle(specialty="research", current_level=1)
        started_again = scheduler.tick_once()
        self.assertFalse(started_again)  # jamais une exception, jamais un doublon
        _wait_terminal(service)

    def test_repeated_ticks_over_time_never_produce_two_running_cycles_at_once(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = _fake_config(enabled=True, interval_s=0.05)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        scheduler.start()
        time.sleep(1.0)
        scheduler.stop()
        _wait_terminal(service)
        # Chaque cycle complété est distinct — aucune preuve de concurrence
        # (une seule ligne d'historique par cycle_id, jamais deux `RUNNING`
        # simultanés puisque `start_cycle` les aurait refusés).
        history = service.history(limit=100)
        cycle_ids = [e.cycle_id for e in history]
        self.assertEqual(len(cycle_ids), len(set(cycle_ids)))
        self.assertGreaterEqual(len(history), 1)


class TestCleanShutdown(unittest.TestCase):
    def test_stop_is_bounded_and_never_hangs(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = _fake_config(enabled=True, interval_s=0.1)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        scheduler.start()
        started = time.monotonic()
        scheduler.stop(timeout_s=5.0)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 6.0)
        self.assertFalse(scheduler.is_running)

    def test_stop_does_not_kill_an_already_running_cycle(self) -> None:
        """`stop()` arrête le déclenchement de NOUVEAUX cycles mais laisse
        un cycle déjà `RUNNING` continuer jusqu'à sa fin naturelle — suivi
        par le service, indépendant du thread du Scheduler."""
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        _promote_a_genome(factory)
        config = _fake_config(enabled=True, interval_s=60)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        scheduler.start()
        deadline = time.monotonic() + 10
        while service.status().state == CycleState.IDLE and time.monotonic() < deadline:
            time.sleep(0.02)
        scheduler.stop()
        final = _wait_terminal(service)
        self.assertEqual(final.state, CycleState.COMPLETED)

    def test_double_start_never_creates_a_second_thread(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        config = _fake_config(enabled=True, interval_s=60)
        service = AutonomousLearningService(factory, agent_runtime=runtime, config=config)
        scheduler = AutonomousLearningScheduler(service, config=config)

        scheduler.start()
        first_thread = scheduler._thread
        scheduler.start()
        self.assertIs(scheduler._thread, first_thread)
        scheduler.stop()


if __name__ == "__main__":
    unittest.main()
