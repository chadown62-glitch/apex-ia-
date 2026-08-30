"""Tests de RecoveryService — réconciliation au démarrage (étape 8).

`ExecutionRegistry` est structurellement vide à l'instanciation d'un nouveau
processus : ces tests simulent donc directement l'état laissé par un
processus précédent (une mission RUNNING, un clone dans un état non
terminal) SANS jamais passer par le Scheduler, exactement comme un vrai
redémarrage le laisserait trouver — puis vérifient que la réconciliation
termine proprement, sans jamais tenter de reprendre quoi que ce soit.
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.model import Clone, CloneState
from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.repositories import AgentRepository, AuditLogRepository
from apex.db.models import Base
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.policy.model import Subject
from apex.scheduler.recovery import RecoveryService


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


class TestRecoveryOfOrphanedMissions(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.recovery = RecoveryService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
        )

    def test_a_running_mission_found_at_startup_is_marked_failed(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        self.mission_service.mark_running(mission.id)

        report = self.recovery.reconcile_on_startup()

        self.assertIn(mission.id, report.orphaned_mission_ids)
        final = self.mission_service.get(mission.id)
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertIn("orphelin", final.error)
        self.assertIn("redémarrage", final.error)

    def test_never_fabricates_a_result_for_a_recovered_mission(self) -> None:
        """Une mission orpheline n'a jamais produit de résultat vérifié —
        `result` doit rester None, jamais rempli par optimisme."""
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        self.mission_service.mark_running(mission.id)
        self.recovery.reconcile_on_startup()
        final = self.mission_service.get(mission.id)
        self.assertIsNone(final.result)

    def test_a_queued_mission_is_left_untouched(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        report = self.recovery.reconcile_on_startup()
        self.assertEqual(report.orphaned_mission_ids, ())
        self.assertEqual(self.mission_service.get(mission.id).state, MissionState.QUEUED)

    def test_a_completed_mission_is_left_untouched(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        self.mission_service.mark_running(mission.id)
        self.mission_service.mark_review(mission.id, result="ok")
        self.mission_service.validate(mission.id, requested_by=Subject.HUMAN)
        report = self.recovery.reconcile_on_startup()
        self.assertEqual(report.orphaned_mission_ids, ())
        self.assertEqual(self.mission_service.get(mission.id).state, MissionState.COMPLETED)


class TestRecoveryOfOrphanedClones(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.recovery = RecoveryService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
        )

    def _force_clone_state(self, clone_id: str, state: CloneState) -> None:
        with session_scope(self.factory) as session:
            AgentRepository(session).update_state(clone_id, state.value)

    def test_a_working_clone_is_recovered_to_failed_not_terminated(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        self._force_clone_state(clone.clone_id, CloneState.WORKING)

        report = self.recovery.reconcile_on_startup()

        self.assertIn(clone.clone_id, report.orphaned_clone_ids)
        with session_scope(self.factory) as session:
            row = AgentRepository(session).get(clone.clone_id)
        self.assertEqual(row.state, CloneState.FAILED.value)
        self.assertIsNotNone(row.terminated_at)
        self.assertIn("orphelin", row.last_error)

    def test_every_non_terminal_state_reaches_a_legal_terminal_state(self) -> None:
        """Couvre chaque état de la table de récupération : le chemin choisi
        doit toujours être une transition LÉGALE de la machine à états
        existante (apex.agents.model), jamais un raccourci hors machine."""
        recoverable_states = (
            CloneState.SPAWNING, CloneState.WORKING, CloneState.REPORTING,
            CloneState.TERMINATING, CloneState.CRASHED, CloneState.RECOVERING,
        )
        for state in recoverable_states:
            with self.subTest(state=state):
                clone = self.runtime.spawn(
                    Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE,
                )
                self._force_clone_state(clone.clone_id, state)
                with session_scope(self.factory) as session:
                    row = AgentRepository(session).get(clone.clone_id)
                    recovered = self.runtime.recover_orphan(
                        Clone.from_row(row), reason="test de récupération",
                    )
                self.assertTrue(recovered.state.is_terminal)

    def test_a_terminated_clone_is_left_untouched(self) -> None:
        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        self.runtime.terminate(clone, reason="fin normale")
        report = self.recovery.reconcile_on_startup()
        self.assertNotIn(clone.clone_id, report.orphaned_clone_ids)


class TestRecoveryLeavesAnExplicitTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.recovery = RecoveryService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
        )

    def test_an_audit_entry_is_written_even_when_nothing_is_orphaned(self) -> None:
        """Consigne étape 8 : « laisser une trace explicite de la
        récupération » — y compris quand il n'y a rien à récupérer, pour
        qu'une réconciliation silencieusement absente ne soit jamais
        confondue avec une réconciliation qui n'a rien trouvé."""
        self.recovery.reconcile_on_startup()
        with session_scope(self.factory) as session:
            entries = AuditLogRepository(session).recent(limit=50)
        matching = [e for e in entries if e.action == "scheduler.recovery_reconciled"]
        self.assertTrue(matching)
        self.assertEqual(matching[0].details["orphaned_mission_ids"], [])
        self.assertEqual(matching[0].details["orphaned_clone_ids"], [])

    def test_recovery_never_attempts_to_relaunch_anything(self) -> None:
        """Aucune reprise automatique : RecoveryService ne doit jamais
        appeler apex.missions.execution.launch_mission — prouvé en le
        remplaçant par un mock qui échouerait le test s'il était appelé."""
        import unittest.mock

        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="x", spawned_by=Subject.CORE)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        self.mission_service.mark_running(mission.id)

        with unittest.mock.patch("apex.missions.execution.launch_mission") as mocked:
            self.recovery.reconcile_on_startup()
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
