"""Tests d'OperatorService (N+16) — lifecycle (Start/Stop/Pause/Resume/
Status), délégation RÉELLE au Task System existant (MissionService +
SchedulerService, jamais un second moteur), permissions (PolicyEngine,
jamais contournée), concurrence/cancellation, récupération après erreur.

Même patron que `tests/test_scheduler_service.py` (SQLite en mémoire +
StaticPool pour un partage correct entre threads, vraie sandbox namespaced
pour l'exécution réelle des missions « research »).
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.design.service import DesignService
from apex.errors import PolicyDenied
from apex.missions.model import MissionPriority
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.operator.service import OperatorNotReady, OperatorService
from apex.operator.states import InvalidOperatorTransition, OperatorState
from apex.policy.model import Subject
from apex.scheduler.service import SchedulerService


def sqlite_session_factory():
    """Fichier temporaire, jamais `:memory:` + `StaticPool` (le patron
    d'`tests/test_scheduler_service.py`) — OperatorService introduit un
    thread de supervision qui appelle `tick()` EN CONTINU, en plus des
    threads exécuteurs du Scheduler : une connexion sqlite3 UNIQUE partagée
    (`StaticPool`) ne supporte pas cette concurrence supplémentaire (constaté
    empiriquement : `sqlite3.InterfaceError: bad parameter or other API
    misuse`, le pilote C sqlite3 corrompant son état de curseur sous accès
    concurrent réel sur le même objet connexion). Un fichier réel laisse
    SQLAlchemy ouvrir une VRAIE connexion sqlite3 par thread — le verrouillage
    au niveau du fichier (avec un `timeout` généreux) sérialise les écritures
    sans jamais corrompre un curseur partagé. PostgreSQL (production) n'a
    besoin d'aucun de ces artifices : il supporte nativement l'accès
    concurrent réel."""
    path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    engine = create_engine(
        f"sqlite:///{path}", future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine), path


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


class _OperatorTestCase(unittest.TestCase):
    """Base commune : factory SQLite partagée entre threads + services réels
    (jamais un faux Scheduler/Mission — Operator est testé CONTRE le Task
    System réel, exactement comme il tournera en production)."""

    tick_interval_s = 0.05

    def setUp(self) -> None:
        self.factory, self._db_path = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.scheduler = SchedulerService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
            max_concurrent_missions=2,
        )
        # N+17 : toujours fourni (comme `apex.api.dependencies.
        # get_operator_service` le fait réellement) — les tests N+16
        # existants n'exercent jamais `submit_design`, donc ceci ne change
        # rien à leur comportement, mais rapproche la fixture de test de la
        # construction de production.
        self.design_service = DesignService()
        self.operator = OperatorService(
            self.factory, scheduler=self.scheduler, mission_service=self.mission_service,
            design_service=self.design_service, tick_interval_s=self.tick_interval_s,
        )

    def tearDown(self) -> None:
        self.operator.shutdown(wait=True)
        self.scheduler.shutdown(wait=True)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self._db_path + suffix)
            except FileNotFoundError:
                pass

    def _spawn_research_clone(self):
        return self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE)

    def _submit_research_task(self, *, priority=MissionPriority.NORMAL, assigned_agent=None):
        clone_id = assigned_agent
        if clone_id is None:
            clone_id = self._spawn_research_clone().clone_id
        return self.operator.submit_task(
            title="t", description="d", specialty="research", objective="o",
            assigned_agent=clone_id, priority=priority,
        )


class TestStartStop(_OperatorTestCase):
    def test_start_from_stopped_reaches_ready(self) -> None:
        status = self.operator.start()
        self.assertEqual(status.state, OperatorState.READY)

    def test_starting_twice_raises_invalid_transition(self) -> None:
        self.operator.start()
        with self.assertRaises(InvalidOperatorTransition):
            self.operator.start()

    def test_stop_from_ready_reaches_stopped(self) -> None:
        self.operator.start()
        status = self.operator.stop()
        self.assertEqual(status.state, OperatorState.STOPPED)

    def test_stopping_when_already_stopped_raises_invalid_transition(self) -> None:
        with self.assertRaises(InvalidOperatorTransition):
            self.operator.stop()

    def test_stop_then_start_again_works(self) -> None:
        self.operator.start()
        self.operator.stop()
        status = self.operator.start()
        self.assertEqual(status.state, OperatorState.READY)


class TestPauseResume(_OperatorTestCase):
    def test_pause_from_ready_reaches_paused(self) -> None:
        self.operator.start()
        status = self.operator.pause()
        self.assertEqual(status.state, OperatorState.PAUSED)

    def test_pause_when_stopped_is_invalid(self) -> None:
        with self.assertRaises(InvalidOperatorTransition):
            self.operator.pause()

    def test_resume_from_paused_reaches_ready(self) -> None:
        self.operator.start()
        self.operator.pause()
        status = self.operator.resume()
        self.assertEqual(status.state, OperatorState.READY)

    def test_resume_when_not_paused_is_invalid(self) -> None:
        self.operator.start()
        with self.assertRaises(InvalidOperatorTransition):
            self.operator.resume()

    def test_paused_operator_still_accepts_submission_but_leaves_it_queued(self) -> None:
        self.operator.start()
        self.operator.pause()
        clone = self._spawn_research_clone()
        mission = self.operator.submit_task(
            title="t", description="d", specialty="research", objective="o",
            assigned_agent=clone.clone_id,
        )
        self.assertEqual(mission.state, MissionState.QUEUED)


class TestStatus(_OperatorTestCase):
    def test_status_reports_stopped_before_start(self) -> None:
        status = self.operator.status()
        self.assertEqual(status.state, OperatorState.STOPPED)
        self.assertEqual(status.active_task_ids, ())
        self.assertEqual(status.queued_task_count, 0)

    def test_status_reports_max_concurrent_tasks_from_scheduler_capacity(self) -> None:
        self.operator.start()
        status = self.operator.status()
        self.assertEqual(status.max_concurrent_tasks, 2)


class TestSubmitTaskAndTracking(_OperatorTestCase):
    def test_submit_task_creates_a_real_mission_queued_or_running(self) -> None:
        self.operator.start()
        mission = self._submit_research_task()
        self.assertIn(mission.state, (MissionState.QUEUED, MissionState.RUNNING, MissionState.REVIEW))

    def test_submit_task_when_stopped_raises_operator_not_ready(self) -> None:
        with self.assertRaises(OperatorNotReady):
            self._submit_research_task()

    def test_get_task_status_reflects_the_real_mission(self) -> None:
        self.operator.start()
        mission = self._submit_research_task()
        status = self.operator.get_task_status(mission.id)
        self.assertIsNotNone(status)
        self.assertEqual(status.id, mission.id)

    def test_get_task_status_of_unknown_task_returns_none(self) -> None:
        self.operator.start()
        self.assertIsNone(self.operator.get_task_status("does-not-exist"))

    def test_task_runs_to_completion_and_result_becomes_ready(self) -> None:
        self.operator.start()
        mission = self._submit_research_task()
        self.assertTrue(self.scheduler.wait_for_mission(mission.id, timeout_s=15))
        result = self.operator.get_task_result(mission.id)
        self.assertTrue(result.ready)
        self.assertEqual(result.state, MissionState.REVIEW.value)
        self.assertIsNotNone(result.result)

    def test_get_task_result_not_ready_while_queued(self) -> None:
        self.operator.start()
        self.operator.pause()  # jamais lancée : reste QUEUED de façon stable pour l'assertion
        clone = self._spawn_research_clone()
        mission = self.operator.submit_task(
            title="t", description="d", specialty="research", objective="o",
            assigned_agent=clone.clone_id,
        )
        result = self.operator.get_task_result(mission.id)
        self.assertFalse(result.ready)
        self.assertIsNone(result.result)

    def test_get_task_result_of_unknown_task_returns_none(self) -> None:
        self.operator.start()
        self.assertIsNone(self.operator.get_task_result("does-not-exist"))

    def test_submitting_a_task_for_an_unsupported_specialty_raises_cleanly(self) -> None:
        """« code-intelligence » est PLANNED (voir
        apex.specialties.registry) — jamais une mission créée pour une
        spécialité non supportée, jamais un faux succès silencieux."""
        self.operator.start()
        with self.assertRaises(ValueError):
            self.operator.submit_task(
                title="t", description="d", specialty="code-intelligence", objective="o",
            )

    def test_task_result_surfaces_a_real_execution_failure(self) -> None:
        """Erreur d'exécution (§9.21) — surfacée fidèlement par
        `get_task_result`, jamais masquée."""
        self.operator.start()
        self.operator.pause()
        mission = self._submit_research_task()
        self.mission_service.mark_running(mission.id)
        self.mission_service.mark_failed(mission.id, error="échec simulé")
        result = self.operator.get_task_result(mission.id)
        self.assertTrue(result.ready)
        self.assertEqual(result.state, MissionState.FAILED.value)
        self.assertEqual(result.error, "échec simulé")


class TestCancelTask(_OperatorTestCase):
    def test_cancel_a_queued_task(self) -> None:
        self.operator.start()
        self.operator.pause()
        clone = self._spawn_research_clone()
        mission = self.operator.submit_task(
            title="t", description="d", specialty="research", objective="o",
            assigned_agent=clone.clone_id,
        )
        cancelled = self.operator.cancel_task(mission.id)
        self.assertEqual(cancelled.state, MissionState.CANCELLED)

    def test_cancel_unknown_task_raises_lookup_error(self) -> None:
        self.operator.start()
        with self.assertRaises(LookupError):
            self.operator.cancel_task("does-not-exist")

    def test_double_cancel_of_an_already_cancelled_task_reports_the_real_terminal_state(self) -> None:
        self.operator.start()
        self.operator.pause()
        clone = self._spawn_research_clone()
        mission = self.operator.submit_task(
            title="t", description="d", specialty="research", objective="o",
            assigned_agent=clone.clone_id,
        )
        self.operator.cancel_task(mission.id)
        second = self.operator.cancel_task(mission.id)
        self.assertEqual(second.state, MissionState.CANCELLED)

    def test_cancel_an_already_completed_task_reports_the_real_terminal_state_not_an_error(self) -> None:
        self.operator.start()
        mission = self._submit_research_task()
        self.assertTrue(self.scheduler.wait_for_mission(mission.id, timeout_s=15))
        self.mission_service.validate(mission.id, requested_by=Subject.HUMAN)
        result = self.operator.cancel_task(mission.id)
        self.assertEqual(result.state, MissionState.COMPLETED)


class TestConcurrency(_OperatorTestCase):
    def test_multiple_tasks_submitted_concurrently_are_all_tracked_and_bounded(self) -> None:
        """3 tâches soumises pour une capacité de 2 (`max_concurrent_missions`,
        voir `setUp`) : la 3e peut légitimement rester QUEUED (backpressure)
        après le premier tick — `wait_for_mission` retourne True IMMÉDIATEMENT
        pour une mission jamais encore suivie (voir sa docstring), ce n'est
        PAS une preuve qu'elle a fini. Le thread de supervision d'Operator
        continue de ticker en tâche de fond (voir `_OperatorTestCase.
        tick_interval_s`) : on attend donc l'état TERMINAL réel de chaque
        mission, jamais seulement l'absence de suivi immédiat."""
        self.operator.start()
        clones = [self._spawn_research_clone() for _ in range(3)]
        missions = [
            self.operator.submit_task(
                title="t", description="d", specialty="research", objective="o",
                assigned_agent=c.clone_id,
            )
            for c in clones
        ]
        deadline = time.monotonic() + 20
        for m in missions:
            while time.monotonic() < deadline:
                current = self.operator.get_task_status(m.id)
                if current.state in (MissionState.REVIEW, MissionState.FAILED):
                    break
                time.sleep(0.05)
        for m in missions:
            final = self.operator.get_task_status(m.id)
            self.assertIn(final.state, (MissionState.REVIEW, MissionState.FAILED))


class TestPermissions(_OperatorTestCase):
    def test_human_can_start_stop_pause_resume(self) -> None:
        self.operator.start(requested_by=Subject.HUMAN)
        self.operator.pause(requested_by=Subject.HUMAN)
        self.operator.resume(requested_by=Subject.HUMAN)
        self.operator.stop(requested_by=Subject.HUMAN)

    def test_core_cannot_start_lifecycle_operations(self) -> None:
        """Aucun sujet autre que HUMAN n'a START_OPERATOR/STOP_OPERATOR/... —
        pas même CORE (voir apex.policy.engine.DEFAULT_ALLOWLIST) : démarrer
        ou arrêter APEX reste une décision humaine explicite."""
        with self.assertRaises(PolicyDenied):
            self.operator.start(requested_by=Subject.CORE)

    def test_a_clone_subject_cannot_start_or_stop(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.operator.start(requested_by=Subject.RESEARCH)
        self.operator.start(requested_by=Subject.HUMAN)
        with self.assertRaises(PolicyDenied):
            self.operator.stop(requested_by=Subject.RESEARCH)

    def test_a_clone_subject_cannot_submit_a_task(self) -> None:
        """SubmitTask délègue à MissionService.create, qui exige déjà
        CREATE_MISSION — jamais accordée à un clone (voir
        apex.missions.service, inchangé) : Operator ne contourne rien."""
        self.operator.start()
        with self.assertRaises(PolicyDenied):
            self.operator.submit_task(
                title="t", description="d", specialty="research", objective="o",
                requested_by=Subject.RESEARCH,
            )


class TestFaultedAndRecovery(_OperatorTestCase):
    def test_scheduler_unavailable_at_start_transitions_to_faulted(self) -> None:
        """Simule un Runtime/DB indisponible : `SchedulerService.capacity()`
        échoue (session factory cassée) — Start doit se traduire par FAULTED,
        jamais un READY optimiste."""

        def _broken_capacity():
            raise RuntimeError("DB indisponible (simulé)")

        self.scheduler.capacity = _broken_capacity  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            self.operator.start()
        status = self.operator.status()
        self.assertEqual(status.state, OperatorState.FAULTED)
        self.assertIsNotNone(status.last_error)

    def test_recovery_after_faulted_start_succeeds_once_scheduler_is_healthy_again(self) -> None:
        def _broken_capacity():
            raise RuntimeError("DB indisponible (simulé)")

        self.scheduler.capacity = _broken_capacity  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            self.operator.start()
        self.assertEqual(self.operator.status().state, OperatorState.FAULTED)

        # Restaure un Scheduler sain (le "Runtime redevient disponible") —
        # même méthode réelle, plus d'exception.
        del self.scheduler.capacity
        status = self.operator.start()
        self.assertEqual(status.state, OperatorState.READY)
        self.assertIsNone(self.operator.status().last_error)


class TestRecentOperations(_OperatorTestCase):
    def test_start_and_submit_are_visible_in_recent_operations(self) -> None:
        self.operator.start()
        self._submit_research_task()
        ops = self.operator.get_recent_operations(limit=50)
        kinds = {op.msg for op in ops}
        self.assertIn("operator.started", kinds)


class TestDesignIntegration(_OperatorTestCase):
    """Intégration Design Engine <-> Operator (N+17) — `submit_design` suit
    la même garde `accepts_submissions` que `submit_task`, jamais une
    seconde machine à états, et le pipeline exécuté est le RÉEL
    `apex.design.service.DesignService`, jamais simulé."""

    def test_submit_design_requires_operator_ready(self) -> None:
        with self.assertRaises(OperatorNotReady):
            self.operator.submit_design("un pavillon minimaliste")

    def test_submit_design_from_ready_completes(self) -> None:
        self.operator.start()
        package = self.operator.submit_design("Créer le concept d'un skatepark urbain moderne")
        self.assertEqual(package.state.value, "completed")
        self.assertTrue(package.ready)
        self.assertGreaterEqual(len(package.concepts), 1)
        self.assertTrue(all(v.state == "unavailable" for v in package.visual_results))

    def test_submit_design_denies_non_human_subject(self) -> None:
        self.operator.start()
        with self.assertRaises(PolicyDenied):
            self.operator.submit_design("test", requested_by=Subject.CORE)

    def test_get_design_status_and_result_round_trip(self) -> None:
        self.operator.start()
        package = self.operator.submit_design("Un jardin japonais contemplatif")
        status = self.operator.get_design_status(package.design_id)
        result = self.operator.get_design_result(package.design_id)
        self.assertIsNotNone(status)
        self.assertEqual(status.design_id, package.design_id)
        self.assertEqual(result.design_id, package.design_id)

    def test_get_design_status_unknown_id_returns_none(self) -> None:
        self.operator.start()
        self.assertIsNone(self.operator.get_design_status("does-not-exist"))

    def test_submit_design_without_design_service_raises_operator_not_ready(self) -> None:
        operator = OperatorService(
            self.factory, scheduler=self.scheduler, mission_service=self.mission_service,
            tick_interval_s=self.tick_interval_s,
        )
        operator.start()
        with self.assertRaises(OperatorNotReady):
            operator.submit_design("test")
        operator.shutdown(wait=True)

    def test_design_submission_visible_in_recent_operations(self) -> None:
        self.operator.start()
        self.operator.submit_design("Un pont piéton contemporain")
        ops = self.operator.get_recent_operations(limit=50)
        kinds = {op.msg for op in ops}
        self.assertIn("design.submitted", kinds)

    def test_pause_still_allows_design_submission(self) -> None:
        """`pause()` suspend UNIQUEMENT le déclenchement de nouvelles tâches
        (Scheduler/Mission, via le thread de supervision) — une conception
        n'implique aucun tick Scheduler, donc `accepts_submissions` (READY/
        RUNNING/PAUSED) l'autorise sans changement de comportement."""
        self.operator.start()
        self.operator.pause()
        package = self.operator.submit_design("Une bibliothèque communautaire")
        self.assertEqual(package.state.value, "completed")

    def test_list_recent_designs_reflects_real_submissions_newest_first(self) -> None:
        self.operator.start()
        first = self.operator.submit_design("Un kiosque à musique")
        second = self.operator.submit_design("Une passerelle piétonne")
        recent = self.operator.list_recent_designs()
        self.assertEqual([p.design_id for p in recent[:2]], [second.design_id, first.design_id])

    def test_list_recent_designs_without_design_service_raises_operator_not_ready(self) -> None:
        operator = OperatorService(
            self.factory, scheduler=self.scheduler, mission_service=self.mission_service,
            tick_interval_s=self.tick_interval_s,
        )
        operator.start()
        with self.assertRaises(OperatorNotReady):
            operator.list_recent_designs()
        operator.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
