"""Tests d'apex.missions.execution.launch_mission — intégration réelle
(SQLite en mémoire + vraie sandbox namespaced, pas de mock de l'exécution).

Vérifie la chaîne complète CORE -> POLICY -> AGENT RUNTIME -> SANDBOX ->
MISSION -> RESULT, et surtout qu'elle ne peut JAMAIS être contournée :
aucun raccourci QUEUED -> COMPLETED, aucune exécution sans autorisation.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PolicyDenied
from apex.missions.execution import launch_mission
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.policy.model import Subject


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


class TestLaunchMissionFullChain(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        self.mission = self.mission_service.create(
            title="Diagnostic", description="d", specialty="research", objective="valider la chaîne",
            created_by=Subject.HUMAN, assigned_agent=self.clone.clone_id,
        )

    def test_full_chain_reaches_review_with_a_real_result(self) -> None:
        self.assertEqual(self.mission.state, MissionState.QUEUED)
        final = launch_mission(
            self.mission.id, session_factory=self.factory,
            mission_service=self.mission_service, agent_runtime=self.runtime,
        )
        self.assertEqual(final.state, MissionState.REVIEW)
        self.assertIsNotNone(final.result)
        self.assertIn("network_reachable", final.result)
        self.assertIn("false", final.result.lower())  # preuve d'isolation depuis l'INTÉRIEUR

    def test_never_jumps_directly_to_completed(self) -> None:
        """§5 de la consigne étape 7 : QUEUED -> COMPLETED directement est interdit."""
        final = launch_mission(
            self.mission.id, session_factory=self.factory,
            mission_service=self.mission_service, agent_runtime=self.runtime,
        )
        self.assertNotEqual(final.state, MissionState.COMPLETED)
        self.assertEqual(final.state, MissionState.REVIEW)

    def test_completed_requires_human_validation_afterwards(self) -> None:
        reviewed = launch_mission(
            self.mission.id, session_factory=self.factory,
            mission_service=self.mission_service, agent_runtime=self.runtime,
        )
        self.assertEqual(reviewed.state, MissionState.REVIEW)
        completed = self.mission_service.validate(self.mission.id, requested_by=Subject.HUMAN)
        self.assertEqual(completed.state, MissionState.COMPLETED)

    def test_clone_reaches_terminated_state(self) -> None:
        """`launch_mission` récupère son propre `Clone` depuis la DB (une
        copie indépendante de celle que ce test détient) : c'est l'état
        PERSISTÉ qu'il faut vérifier, pas la référence Python locale, restée
        périmée — même principe que Clone.from_row (apex/agents/model.py)."""
        launch_mission(
            self.mission.id, session_factory=self.factory,
            mission_service=self.mission_service, agent_runtime=self.runtime,
        )
        from apex.agents.model import Clone, CloneState
        from apex.db.engine import session_scope
        from apex.db.repositories import AgentRepository

        with session_scope(self.factory) as session:
            row = AgentRepository(session).get(self.clone.clone_id)
            persisted = Clone.from_row(row)
        self.assertEqual(persisted.state, CloneState.TERMINATED)

    def test_events_reconstruct_the_full_timeline(self) -> None:
        """§7 : chaque exécution doit produire des événements exploitables."""
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        launch_mission(
            self.mission.id, session_factory=self.factory,
            mission_service=self.mission_service, agent_runtime=self.runtime,
        )
        with session_scope(self.factory) as session:
            events = EventRepository(session).recent(limit=200)
        kinds = [e.msg for e in events if e.context.get("mission_id") == self.mission.id]
        for expected in ("mission.created", "mission.queued", "mission.started",
                          "sandbox.created", "sandbox.started", "sandbox.completed", "mission.review"):
            with self.subTest(kind=expected):
                self.assertIn(expected, kinds)


class TestLaunchMissionPolicyCannotBeBypassed(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE,
        )
        self.mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=self.clone.clone_id,
        )

    def test_a_clone_cannot_launch_its_own_mission(self) -> None:
        with self.assertRaises(PolicyDenied):
            launch_mission(
                self.mission.id, session_factory=self.factory,
                mission_service=self.mission_service, agent_runtime=self.runtime,
                requested_by=Subject.RESEARCH,
            )
        # La mission n'a pas avancé : refus AVANT toute transition d'état.
        self.assertEqual(self.mission_service.get(self.mission.id).state, MissionState.QUEUED)

    def test_human_cannot_launch_directly_either(self) -> None:
        """Seul CORE lance réellement — HUMAN valide/rejette en aval, ne
        déclenche jamais lui-même l'exécution (chaîne obligatoire)."""
        with self.assertRaises(PolicyDenied):
            launch_mission(
                self.mission.id, session_factory=self.factory,
                mission_service=self.mission_service, agent_runtime=self.runtime,
                requested_by=Subject.HUMAN,
            )


class TestLaunchMissionPreflightValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)

    def test_nonexistent_mission_raises(self) -> None:
        with self.assertRaises(LookupError):
            launch_mission(
                "does-not-exist", session_factory=self.factory,
                mission_service=self.mission_service, agent_runtime=self.runtime,
            )

    def test_mission_without_assigned_agent_raises(self) -> None:
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        with self.assertRaises(ValueError):
            launch_mission(
                mission.id, session_factory=self.factory,
                mission_service=self.mission_service, agent_runtime=self.runtime,
            )
        self.assertEqual(self.mission_service.get(mission.id).state, MissionState.QUEUED)

    def test_clone_not_ready_raises(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        # Épuiser le clone une première fois -> TERMINATED, plus READY.
        self.runtime.run(clone, lambda: None)
        with self.assertRaises(ValueError):
            launch_mission(
                mission.id, session_factory=self.factory,
                mission_service=self.mission_service, agent_runtime=self.runtime,
            )
        self.assertEqual(self.mission_service.get(mission.id).state, MissionState.QUEUED)


class TestLaunchMissionSandboxUnavailable(unittest.TestCase):
    def test_unavailable_sandbox_fails_the_mission_explicitly(self) -> None:
        from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
        from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport

        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        mission_service = MissionService(factory)
        clone = runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        fake_report = SandboxCapabilityReport(
            level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
            privilege_drop_available=False, network_isolation_available=False,
            docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
        )
        unavailable_sandbox = LinuxNamespaceSandbox(capability_report=fake_report)

        final = launch_mission(
            mission.id, session_factory=factory, mission_service=mission_service,
            agent_runtime=runtime, sandbox=unavailable_sandbox,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertIn("sandbox indisponible", final.error)


if __name__ == "__main__":
    unittest.main()
