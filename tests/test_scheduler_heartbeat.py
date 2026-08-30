"""Tests de HeartbeatService — vivacité RÉELLE, jamais supposée.

Le battement de cœur est un constat noyau direct (`CancellationToken.
is_alive()`, lui-même `os.killpg(pgid, 0)`) — ces tests l'exercent contre
une vraie exécution sandboxée, pas un mock du processus.
"""

from __future__ import annotations

import threading
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import AgentRepository
from apex.missions.execution import launch_mission
from apex.missions.service import MissionService
from apex.policy.model import Subject
from apex.scheduler.heartbeat import HeartbeatService
from apex.scheduler.model import ExecutionHandle
from apex.scheduler.registry import ExecutionRegistry


def sqlite_session_factory():
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


class TestHeartbeatAgainstARealExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)

    def test_heartbeat_timestamps_really_advance_during_a_live_execution(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        registry = ExecutionRegistry()
        handle = ExecutionHandle(mission_id=mission.id, clone_id=clone.clone_id)
        registry.track(handle)
        heartbeat = HeartbeatService(self.factory, registry=registry, interval_s=0.2)

        started = threading.Event()

        def run() -> None:
            started.set()
            launch_mission(
                mission.id, session_factory=self.factory, mission_service=self.mission_service,
                agent_runtime=self.runtime, command=("python3", "-c", "import time; time.sleep(2)"),
                cancel_token=handle.cancel_token,
            )

        t = threading.Thread(target=run)
        t.start()
        started.wait(timeout=5)
        time.sleep(0.3)  # laisser le vrai sous-processus démarrer

        timestamps = []
        for _ in range(4):
            written = heartbeat.tick_once()
            self.assertEqual(written, 1, "le battement aurait dû être écrit : processus réellement vivant")
            with session_scope(self.factory) as session:
                row = AgentRepository(session).get(clone.clone_id)
                timestamps.append(row.last_heartbeat_at)
            time.sleep(0.3)

        t.join(timeout=10)
        registry.discard(mission.id)

        self.assertEqual(len(set(timestamps)), len(timestamps), "les battements n'ont pas réellement avancé")
        self.assertEqual(sorted(timestamps), timestamps)

    def test_no_heartbeat_written_after_the_process_has_really_died(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        registry = ExecutionRegistry()
        handle = ExecutionHandle(mission_id=mission.id, clone_id=clone.clone_id)
        registry.track(handle)
        heartbeat = HeartbeatService(self.factory, registry=registry, interval_s=0.2)

        launch_mission(
            mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, command=("true",), cancel_token=handle.cancel_token,
        )
        # Le processus est déjà terminé : is_alive() doit le constater, pas
        # supposer qu'il tourne encore parce qu'un ExecutionHandle existe.
        written = heartbeat.tick_once()
        self.assertEqual(written, 0)
        registry.discard(mission.id)


if __name__ == "__main__":
    unittest.main()
