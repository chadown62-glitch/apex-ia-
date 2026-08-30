"""Tests de SchedulerService — priorité/FIFO, dépendances, capacité/
backpressure, réclamation atomique sous concurrence RÉELLE, annulation
réelle (killpg + vérification), non-contournement de la chaîne obligatoire
et de PolicyEngine, absence de retry/résolution d'agent automatiques.

Patron identique à tests/test_missions_execution.py (SQLite en mémoire +
vraie sandbox namespaced) pour tout ce qui ne nécessite pas plusieurs
connexions concurrentes réelles ; PostgreSQL réel (tests/db_test_support)
pour la réclamation atomique sous plusieurs workers/instances — c'est
justement ce qu'une seule connexion SQLite ne peut pas prouver.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import unittest
import unittest.mock

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base
from apex.db.repositories import EventRepository
from apex.errors import PolicyDenied
from apex.missions.model import MissionPriority
from apex.missions.service import MissionService
from apex.missions.states import InvalidMissionTransition, MissionState
from apex.policy.model import Subject
from apex.scheduler.service import SchedulerService
from tests import db_test_support as support

_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


def sqlite_session_factory():
    """StaticPool + check_same_thread=False : indispensable ici, à la
    différence des autres fichiers de test — SchedulerService exécute
    réellement des missions dans d'autres threads (ThreadPoolExecutor).
    Sans ceci, SQLite « :memory: » donne une base DIFFÉRENTE par thread
    (SingletonThreadPool par défaut) et le worker ne verrait jamais les
    lignes créées par le thread principal."""
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


def spawn_research_clone(factory, runtime=None):
    runtime = runtime or AgentRuntime(factory)
    return runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE)


class _SchedulerTestCase(unittest.TestCase):
    """Base commune : factory SQLite partagée entre threads + services."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.scheduler = SchedulerService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
            max_concurrent_missions=2,
        )

    def tearDown(self) -> None:
        self.scheduler.shutdown(wait=True)

    def _mission_with_clone(self, *, priority=MissionPriority.NORMAL, depends_on=()) -> tuple:
        clone = spawn_research_clone(self.factory, self.runtime)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id, priority=priority,
            depends_on=depends_on,
        )
        return mission, clone


class TestPriorityAndFifo(_SchedulerTestCase):
    def test_higher_priority_is_scheduled_first(self) -> None:
        normal, _ = self._mission_with_clone(priority=MissionPriority.NORMAL)
        high, _ = self._mission_with_clone(priority=MissionPriority.HIGH)
        eligible, _blocked = self.scheduler._eligible_queued_missions()
        self.assertEqual(eligible[0].id, high.id)
        self.assertEqual(eligible[1].id, normal.id)

    def test_fifo_within_the_same_priority(self) -> None:
        first, _ = self._mission_with_clone(priority=MissionPriority.NORMAL)
        second, _ = self._mission_with_clone(priority=MissionPriority.NORMAL)
        eligible, _blocked = self.scheduler._eligible_queued_missions()
        ids = [m.id for m in eligible]
        self.assertLess(ids.index(first.id), ids.index(second.id))

    def test_critical_beats_high_beats_normal_beats_low(self) -> None:
        low, _ = self._mission_with_clone(priority=MissionPriority.LOW)
        critical, _ = self._mission_with_clone(priority=MissionPriority.CRITICAL)
        normal, _ = self._mission_with_clone(priority=MissionPriority.NORMAL)
        high, _ = self._mission_with_clone(priority=MissionPriority.HIGH)
        eligible, _blocked = self.scheduler._eligible_queued_missions()
        self.assertEqual([m.id for m in eligible], [critical.id, high.id, normal.id, low.id])


class TestDependencyEligibility(_SchedulerTestCase):
    def test_mission_with_unmet_dependency_is_blocked_not_submitted(self) -> None:
        upstream, _ = self._mission_with_clone()
        downstream, _ = self._mission_with_clone(depends_on=(upstream.id,))
        eligible, blocked = self.scheduler._eligible_queued_missions()
        self.assertNotIn(downstream.id, [m.id for m in eligible])
        self.assertIn(downstream.id, blocked)

    def test_blocked_mission_is_never_deleted_or_silently_lost(self) -> None:
        upstream, _ = self._mission_with_clone()
        downstream, _ = self._mission_with_clone(depends_on=(upstream.id,))
        for _ in range(3):
            self.scheduler.tick()
        self.assertTrue(self.scheduler.wait_for_idle(timeout_s=10))
        still_there = self.mission_service.get(downstream.id)
        self.assertIsNotNone(still_there)
        self.assertEqual(still_there.state, MissionState.QUEUED)

    def test_mission_becomes_eligible_once_dependency_is_completed(self) -> None:
        upstream, _ = self._mission_with_clone()
        downstream, _ = self._mission_with_clone(depends_on=(upstream.id,))

        self.mission_service.mark_running(upstream.id)
        self.mission_service.mark_review(upstream.id, result="ok")
        self.mission_service.validate(upstream.id, requested_by=Subject.HUMAN)

        eligible, blocked = self.scheduler._eligible_queued_missions()
        self.assertIn(downstream.id, [m.id for m in eligible])
        self.assertNotIn(downstream.id, blocked)


class TestNoAutomaticAgentResolution(_SchedulerTestCase):
    def test_mission_without_assigned_agent_is_never_launched(self) -> None:
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o", created_by=Subject.HUMAN,
        )
        result = self.scheduler.tick()
        self.assertIn(mission.id, result.skipped_no_agent)
        self.assertNotIn(mission.id, result.submitted)
        self.assertTrue(self.scheduler.wait_for_idle(timeout_s=5))
        still_queued = self.mission_service.get(mission.id)
        self.assertEqual(still_queued.state, MissionState.QUEUED)


class TestCapacityAndBackpressure(_SchedulerTestCase):
    def test_capacity_bounds_concurrently_submitted_missions(self) -> None:
        scheduler = SchedulerService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
            max_concurrent_missions=1,
        )
        self.addCleanup(scheduler.shutdown)
        a, _ = self._mission_with_clone()
        b, _ = self._mission_with_clone()
        result = scheduler.tick()
        self.assertEqual(len(result.submitted), 1)
        self.assertEqual(len(result.skipped_no_capacity), 1)
        scheduler.wait_for_idle(timeout_s=10)

    def test_mission_skipped_for_capacity_is_never_lost_and_is_retried_later(self) -> None:
        scheduler = SchedulerService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
            max_concurrent_missions=1,
        )
        self.addCleanup(scheduler.shutdown)
        a, _ = self._mission_with_clone()
        b, _ = self._mission_with_clone()
        first = scheduler.tick()
        self.assertTrue(scheduler.wait_for_idle(timeout_s=10))
        # b n'a jamais été supprimée : un tick ultérieur la reprend.
        second = scheduler.tick()
        self.assertTrue(scheduler.wait_for_idle(timeout_s=10))
        submitted_overall = set(first.submitted) | set(second.submitted)
        self.assertEqual(submitted_overall, {a.id, b.id})

    def test_executor_never_creates_more_threads_than_max_concurrent(self) -> None:
        """Garantie structurelle : quel que soit le nombre de missions
        soumises, le pool de threads reste borné — pas un compteur applicatif
        à respecter, une propriété de ThreadPoolExecutor(max_workers=...)."""
        scheduler = SchedulerService(
            self.factory, mission_service=self.mission_service, agent_runtime=self.runtime,
            max_concurrent_missions=2,
        )
        self.addCleanup(scheduler.shutdown)
        self.assertEqual(scheduler._executor._max_workers, 2)


class TestNoAutomaticRetry(_SchedulerTestCase):
    def test_a_failed_mission_is_never_relaunched_by_a_later_tick(self) -> None:
        mission, clone = self._mission_with_clone()
        self.mission_service.mark_running(mission.id)
        self.mission_service.mark_failed(mission.id, error="échec simulé")
        for _ in range(3):
            result = self.scheduler.tick()
            self.assertNotIn(mission.id, result.submitted)
        self.assertTrue(self.scheduler.wait_for_idle(timeout_s=5))
        final = self.mission_service.get(mission.id)
        self.assertEqual(final.state, MissionState.FAILED)


class TestChainNeverBypassed(_SchedulerTestCase):
    def test_tick_only_ever_calls_the_audited_launch_mission_entrypoint(self) -> None:
        """Le Scheduler ne doit JAMAIS appeler AgentRuntime/SandboxProvider
        directement — seulement apex.missions.execution.launch_mission
        (étape 7, déjà audité). On le prouve en remplaçant ce point d'entrée
        par un mock : si tick() fonctionne toujours et que le mock a bien
        été appelé avec requested_by=Subject.CORE, aucun autre chemin n'a
        été emprunté."""
        mission, _clone = self._mission_with_clone()
        with unittest.mock.patch("apex.scheduler.service.launch_mission") as mocked:
            mocked.return_value = self.mission_service.get(mission.id)
            self.scheduler.tick()
            self.scheduler.wait_for_idle(timeout_s=5)
        mocked.assert_called_once()
        _args, kwargs = mocked.call_args
        self.assertIs(kwargs["requested_by"], Subject.CORE)
        self.assertEqual(kwargs["mission_service"], self.mission_service)
        self.assertEqual(kwargs["agent_runtime"], self.runtime)

    def test_cancel_mission_authorization_is_never_duplicated_or_bypassed(self) -> None:
        """L'autorisation d'annuler reste ENTIÈREMENT déléguée à
        MissionService.cancel (Action.CANCEL_MISSION) — un clone refusé ici
        prouve qu'aucun chemin alternatif du Scheduler ne l'accorderait."""
        mission, _clone = self._mission_with_clone()
        with self.assertRaises(PolicyDenied):
            self.scheduler.cancel_mission(mission.id, requested_by=Subject.RESEARCH)

    def test_core_and_human_can_cancel(self) -> None:
        for subject in (Subject.CORE, Subject.HUMAN):
            with self.subTest(subject=subject):
                mission, _clone = self._mission_with_clone()
                cancelled = self.scheduler.cancel_mission(mission.id, requested_by=subject)
                self.assertEqual(cancelled.state, MissionState.CANCELLED)


class TestCancellationReal(_SchedulerTestCase):
    """Annulation réelle d'une mission RUNNING : os.killpg, vérifiée par
    constat direct (pas supposée), aucun processus ni cgroup laissé derrière
    — même discipline empirique que l'étape 7."""

    def _cgroup_dir_count(self) -> int:
        total = 0
        for controller in ("cpu", "memory", "pids"):
            base = f"/sys/fs/cgroup/{controller}"
            if os.path.isdir(base):
                total += len([d for d in os.listdir(base) if d.startswith("apex_")])
        return total

    def test_cancelling_a_running_mission_kills_the_real_process_group(self) -> None:
        marker = "apex_scheduler_cancel_marker_test"
        clone = spawn_research_clone(self.factory, self.runtime)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        before_cgroups = self._cgroup_dir_count()

        from apex.missions.execution import launch_mission

        started_launch = threading.Event()

        def run() -> None:
            from apex.scheduler.model import ExecutionHandle

            handle = ExecutionHandle(mission_id=mission.id, clone_id=clone.clone_id)
            self.scheduler.registry.track(handle)
            started_launch.set()
            launch_mission(
                mission.id, session_factory=self.factory, mission_service=self.mission_service,
                agent_runtime=self.runtime,
                command=("python3", "-c", f"import time; time.sleep(15)  # {marker}"),
                cancel_token=handle.cancel_token,
            )
            self.scheduler.registry.discard(mission.id)

        t = threading.Thread(target=run)
        t.start()
        started_launch.wait(timeout=5)
        time.sleep(0.8)  # laisser le vrai sous-processus démarrer

        cancelled = self.scheduler.cancel_mission(mission.id, requested_by=Subject.HUMAN)
        t.join(timeout=15)

        self.assertEqual(cancelled.state, MissionState.CANCELLED)

        time.sleep(0.3)
        out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "", f"processus orphelin détecté : {out.stdout}")
        self.assertEqual(self._cgroup_dir_count(), before_cgroups, "fuite de cgroup après annulation")

    def test_cancellation_event_records_a_verified_termination(self) -> None:
        clone = spawn_research_clone(self.factory, self.runtime)
        mission = self.mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )
        from apex.missions.execution import launch_mission
        from apex.scheduler.model import ExecutionHandle

        handle = ExecutionHandle(mission_id=mission.id, clone_id=clone.clone_id)
        self.scheduler.registry.track(handle)
        started = threading.Event()

        def run() -> None:
            started.set()
            launch_mission(
                mission.id, session_factory=self.factory, mission_service=self.mission_service,
                agent_runtime=self.runtime, command=("python3", "-c", "import time; time.sleep(10)"),
                cancel_token=handle.cancel_token,
            )
            self.scheduler.registry.discard(mission.id)

        t = threading.Thread(target=run)
        t.start()
        started.wait(timeout=5)
        time.sleep(0.5)
        self.scheduler.cancel_mission(mission.id, requested_by=Subject.HUMAN)
        t.join(timeout=15)

        with session_scope(self.factory) as session:
            events = EventRepository(session).recent(limit=200)
        matching = [e for e in events if e.msg == "scheduler.cancel_requested" and e.context.get("mission_id") == mission.id]
        self.assertTrue(matching)
        self.assertTrue(matching[-1].context.get("had_tracked_execution"))
        self.assertTrue(matching[-1].context.get("process_group_verified_terminated"))


class TestWaitForMission(_SchedulerTestCase):
    """`wait_for_mission` — étape 11, utilisé par `POST /missions/{id}/launch`
    pour attendre l'issue d'UNE mission précise sans bloquer sur les autres
    qu'un même tick() a pu soumettre. Même patron que `wait_for_idle`, testé
    séparément parce qu'il filtre sur un seul mission_id."""

    def test_returns_true_immediately_for_a_mission_never_tracked(self) -> None:
        self.assertTrue(self.scheduler.wait_for_mission("never-submitted", timeout_s=1))

    def test_returns_true_once_the_submitted_mission_completes(self) -> None:
        mission, _clone = self._mission_with_clone()
        result = self.scheduler.tick()
        self.assertIn(mission.id, result.submitted)
        self.assertTrue(self.scheduler.wait_for_mission(mission.id, timeout_s=10))
        self.assertNotIn(mission.id, self.scheduler.registry)
        final = self.mission_service.get(mission.id)
        self.assertEqual(final.state, MissionState.REVIEW)

    def test_does_not_block_on_a_different_tracked_mission(self) -> None:
        """Preuve que le filtre porte bien sur UN mission_id, pas sur
        `wait_for_idle` déguisé : une autre mission encore suivie ne doit
        jamais faire attendre `wait_for_mission` au-delà de son échéance."""
        from apex.scheduler.model import ExecutionHandle

        other_clone = spawn_research_clone(self.factory, self.runtime)
        never_finishes = ExecutionHandle(mission_id="stuck-forever", clone_id=other_clone.clone_id)
        self.scheduler.registry.track(never_finishes)
        try:
            mission, _clone = self._mission_with_clone()
            result = self.scheduler.tick()
            self.assertIn(mission.id, result.submitted)
            self.assertTrue(self.scheduler.wait_for_mission(mission.id, timeout_s=10))
        finally:
            self.scheduler.registry.discard("stuck-forever")

    def test_times_out_while_the_mission_is_still_tracked(self) -> None:
        from apex.scheduler.model import ExecutionHandle

        clone = spawn_research_clone(self.factory, self.runtime)
        handle = ExecutionHandle(mission_id="still-running", clone_id=clone.clone_id)
        self.scheduler.registry.track(handle)
        try:
            completed = self.scheduler.wait_for_mission("still-running", timeout_s=0.2, poll_interval_s=0.02)
            self.assertFalse(completed)
        finally:
            self.scheduler.registry.discard("still-running")


class TestHeartbeatIndependenceFromMissionState(_SchedulerTestCase):
    def test_no_heartbeat_written_for_a_handle_never_attached_to_a_process(self) -> None:
        """Le heartbeat ne se fie JAMAIS à un état supposé — sans processus
        réel rattaché au token, aucune écriture, même si un ExecutionHandle
        existe dans le registre."""
        from apex.scheduler.heartbeat import HeartbeatService
        from apex.scheduler.model import ExecutionHandle

        clone = spawn_research_clone(self.factory, self.runtime)
        handle = ExecutionHandle(mission_id="never-started", clone_id=clone.clone_id)
        self.scheduler.registry.track(handle)
        heartbeat = HeartbeatService(self.factory, registry=self.scheduler.registry)
        written = heartbeat.tick_once()
        self.assertEqual(written, 0)


class TestCurrentVsTargetMachineSeparation(unittest.TestCase):
    """`apex.scheduler.capacity` — ACTIVE (machine de développement/prod
    réellement disponible) et TARGET (machine cible APEX, pas encore
    construite) ne doivent jamais se mélanger dans le calcul de capacité."""

    def test_target_pools_are_reported_separately_and_never_affect_slots(self) -> None:
        from tests.test_config import config

        from apex.config.loader import parse_config
        from apex.scheduler.capacity import build_capacity_view

        document = config()
        document["compute"]["pools"]["future-apex-machine"] = {
            "location": "local", "trust_level": "trusted", "status": "target",
            "capabilities": ["inference", "sandbox", "cpu_eval"],
            "cpu_cores": 256, "ram_gb": 1024,
        }
        document["scheduler"] = {"max_concurrent_missions": 3}
        cfg = parse_config(document)

        view = build_capacity_view(cfg, running_missions=1)
        active_ids = {p.pool_id for p in view.active_pools}
        target_ids = {p.pool_id for p in view.target_pools}
        self.assertIn("local-gpu", active_ids)
        self.assertIn("future-apex-machine", target_ids)
        self.assertNotIn("future-apex-machine", active_ids)
        # La capacité cible (256 cores) n'apparaît nulle part dans le calcul
        # de créneaux disponibles — seul max_concurrent_missions (config) compte.
        self.assertEqual(view.max_concurrent_missions, 3)
        self.assertEqual(view.available_slots, 2)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestAtomicClaimAgainstRealPostgres(unittest.TestCase):
    """La seule preuve qui compte pour « plusieurs workers ou plusieurs
    instances du Scheduler » : de VRAIES connexions PostgreSQL concurrentes,
    pas un seul verrou SQLite partagé qui prouverait surtout que SQLite
    sérialise — voir la docstring du module pour ce choix."""

    def setUp(self) -> None:
        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def test_only_one_of_many_concurrent_claims_succeeds(self) -> None:
        factory = self._factory()
        mission_service = MissionService(factory)
        runtime = AgentRuntime(factory)
        clone = spawn_research_clone(factory, runtime)
        mission = mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )

        results: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def attempt() -> None:
            barrier.wait()
            try:
                mission_service.mark_running(mission.id)
                with lock:
                    results.append("claimed")
            except InvalidMissionTransition:
                with lock:
                    results.append("lost")

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(results.count("claimed"), 1, results)
        self.assertEqual(results.count("lost"), 7, results)
        final = mission_service.get(mission.id)
        self.assertEqual(final.state, MissionState.RUNNING)

    def test_two_scheduler_instances_never_launch_the_same_mission_twice(self) -> None:
        factory = self._factory()
        mission_service = MissionService(factory)
        runtime = AgentRuntime(factory)
        clone = spawn_research_clone(factory, runtime)
        mission = mission_service.create(
            title="t", description="d", specialty="research", objective="o",
            created_by=Subject.HUMAN, assigned_agent=clone.clone_id,
        )

        scheduler_a = SchedulerService(
            factory, mission_service=mission_service, agent_runtime=runtime, max_concurrent_missions=1,
        )
        scheduler_b = SchedulerService(
            factory, mission_service=mission_service, agent_runtime=runtime, max_concurrent_missions=1,
        )
        self.addCleanup(scheduler_a.shutdown)
        self.addCleanup(scheduler_b.shutdown)

        barrier = threading.Barrier(2)

        def run(scheduler: SchedulerService) -> None:
            barrier.wait()
            scheduler.tick()

        t_a = threading.Thread(target=run, args=(scheduler_a,))
        t_b = threading.Thread(target=run, args=(scheduler_b,))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)
        scheduler_a.wait_for_idle(timeout_s=15)
        scheduler_b.wait_for_idle(timeout_s=15)

        final = mission_service.get(mission.id)
        # Lancée exactement une fois : REVIEW (succès) — jamais restée QUEUED
        # (les deux auraient échoué) ni un état incohérent.
        self.assertEqual(final.state, MissionState.REVIEW)

        with session_scope(factory) as session:
            events = EventRepository(session).recent(limit=200)
        starts = [
            e for e in events
            if e.msg == "mission.started" and e.context.get("mission_id") == mission.id
        ]
        self.assertEqual(len(starts), 1, "la mission a été lancée plus d'une fois")


if __name__ == "__main__":
    unittest.main()
