"""Tests de la boucle de recovery/retry/reassignment N+26 dans
`apex.missions.execution.launch_mission` — intégration RÉELLE (SQLite en
mémoire + vraie sandbox namespaced pour l'exécution, jamais un mock de
`SandboxProvider.execute`), même discipline que
`tests/test_missions_execution.py`/`tests/test_sandbox_security.py`.

Le chemin historique (`pool=None`) est déjà couvert, inchangé, par
`tests/test_missions_execution.py` — ce fichier vérifie EXCLUSIVEMENT ce qui
est nouveau : la boucle bornée activée par `pool=`."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, PoolLocation, TrustLevel
from apex.db.engine import build_session_factory, session_scope
from apex.db.repositories import AgentRepository, EventRepository
from apex.db.models import Base
from apex.missions.execution import _rotate_pool, launch_mission
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.policy.model import Subject
from apex.sandbox import CancellationToken, ResourceLimits
from apex.sandbox.linux_namespace import LinuxNamespaceSandbox
from apex.sandbox.model import IsolationLevel, SandboxCapabilityReport


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_pool(pool_id: str = "local", **overrides) -> ComputePool:
    defaults = dict(
        pool_id=pool_id, location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.CPU_EVAL,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


def _unavailable_sandbox() -> LinuxNamespaceSandbox:
    fake_report = SandboxCapabilityReport(
        level=IsolationLevel.RLIMIT_ONLY, namespaces_available=False, cgroups_available=False,
        privilege_drop_available=False, network_isolation_available=False,
        docker_daemon_reachable=False, seccomp_available=False, detail="test: forced unavailable",
    )
    return LinuxNamespaceSandbox(capability_report=fake_report)


class _RecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.mission_service = MissionService(self.factory)
        self.pool = a_pool()
        self.clone = self.runtime.spawn(
            Subject.RESEARCH, pool=self.pool, genome_ref="sha256:x", spawned_by=Subject.CORE,
        )
        self.mission = self.mission_service.create(
            title="Recovery test", description="d", specialty="research", objective="objectif",
            created_by=Subject.HUMAN, assigned_agent=self.clone.clone_id,
        )

    def _events(self) -> list[dict]:
        """Ordre CHRONOLOGIQUE — `EventRepository.recent()` trie
        `ts.desc()` (le plus récent d'abord), inversé ici pour lire la
        trace dans l'ordre où les événements se sont réellement produits."""
        with session_scope(self.factory) as session:
            rows = EventRepository(session).recent(limit=200)
        return [e.context for e in reversed(rows)]

    def _clone_count(self) -> int:
        """Nombre TOTAL de clones créés dans ce test (DB SQLite isolée par
        test) — le clone initial de `setUp` n'est jamais tagué avec
        `mission_id` (la mission n'existe pas encore à son instant de
        spawn), donc compter uniquement par `mission_id` sous-compterait."""
        with session_scope(self.factory) as session:
            from apex.db.models import AgentRow

            return session.query(AgentRow).count()


class TestExecutionFailureRetriesThenBlocks(_RecoveryTestCase):
    def test_retries_once_then_blocks_with_two_real_clones(self) -> None:
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(),
            command=("false",), limits=limits, pool=self.pool, max_attempts=2,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertEqual(self._clone_count(), 2, "une tentative initiale + une nouvelle tentative réelle")

        kinds = [e.get("kind") for e in self._events() if e.get("mission_id") == self.mission.id]
        self.assertEqual(kinds.count("retry.started"), 1)
        self.assertEqual(kinds.count("recovery.decided"), 2)
        self.assertEqual(kinds.count("execution.started"), 2)

    def test_recovery_decided_events_carry_the_full_decision(self) -> None:
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(),
            command=("false",), limits=limits, pool=self.pool, max_attempts=2,
        )
        decisions = [e for e in self._events() if e.get("kind") == "recovery.decided"]
        self.assertEqual(decisions[0]["action"], "retry")
        self.assertEqual(decisions[0]["category"], "execution_failure")
        self.assertEqual(decisions[1]["action"], "block")


class TestTimeoutRetriesThenBlocks(_RecoveryTestCase):
    def test_real_timeout_is_classified_and_retried(self) -> None:
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=1, disk_mb=16, network="none")
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(),
            command=("python3", "-c", "import time; time.sleep(10)"), limits=limits,
            pool=self.pool, max_attempts=2,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        decisions = [e for e in self._events() if e.get("kind") == "recovery.decided"]
        self.assertEqual(decisions[0]["category"], "timeout")
        self.assertEqual(decisions[0]["action"], "retry")


class TestSandboxFailureBlocksImmediatelyNoRetry(_RecoveryTestCase):
    def test_isolation_unavailable_blocks_on_first_attempt(self) -> None:
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=_unavailable_sandbox(),
            command=("true",), limits=limits, pool=self.pool, max_attempts=5,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertEqual(self._clone_count(), 1, "SANDBOX_FAILURE bloque immédiatement, jamais de nouvelle tentative")
        kinds = [e.get("kind") for e in self._events() if e.get("mission_id") == self.mission.id]
        self.assertNotIn("retry.started", kinds)
        decisions = [e for e in self._events() if e.get("kind") == "recovery.decided"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["category"], "sandbox_failure")
        self.assertEqual(decisions[0]["action"], "block")


class TestResourceExhaustedUsesStricterBudget(_RecoveryTestCase):
    def test_oom_retries_once_then_blocks_even_with_room_left_in_max_attempts(self) -> None:
        """`resource_exhausted_max_retries` par défaut (1) est plus strict
        que `max_attempts` (ici 5) — la boucle doit s'arrêter après UNE
        seule nouvelle tentative, pas cinq."""
        script = (
            "chunks = []\n"
            "for i in range(2000):\n"
            "    b = bytearray(1024 * 1024)\n"
            "    for j in range(0, len(b), 4096):\n"
            "        b[j] = 1\n"
            "    chunks.append(b)\n"
        )
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=32, pids_limit=16, timeout_s=10, disk_mb=16, network="none")
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(),
            command=("python3", "-c", script), limits=limits, pool=self.pool, max_attempts=5,
        )
        self.assertEqual(final.state, MissionState.FAILED)
        self.assertEqual(self._clone_count(), 2, "une seule nouvelle tentative malgré max_attempts=5")
        decisions = [e for e in self._events() if e.get("kind") == "recovery.decided"]
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0]["category"], "resource_exhausted")
        self.assertEqual(decisions[0]["action"], "retry")
        self.assertEqual(decisions[1]["action"], "block")


class TestCancellationShortCircuitsRecovery(_RecoveryTestCase):
    def test_pre_cancelled_token_never_invokes_recovery_or_mark_failed(self) -> None:
        token = CancellationToken()
        token.request_cancel()  # avant même le démarrage — voir CancellationToken._register
        limits = ResourceLimits(cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none")
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(),
            command=("python3", "-c", "import time; time.sleep(10)"), limits=limits,
            pool=self.pool, max_attempts=3, cancel_token=token,
        )
        # Comme le chemin historique : jamais mark_failed, l'état persisté
        # est renvoyé tel quel (toujours RUNNING — rien n'appelle cancel_mission
        # dans ce test, seul le Scheduler a autorité sur cette transition).
        self.assertEqual(final.state, MissionState.RUNNING)
        self.assertEqual(self._clone_count(), 1, "aucune nouvelle tentative sur une annulation")
        kinds = [e.get("kind") for e in self._events() if e.get("mission_id") == self.mission.id]
        self.assertNotIn("recovery.decided", kinds)
        self.assertNotIn("retry.started", kinds)


class TestSuccessWithPoolProvidedStillWorks(_RecoveryTestCase):
    def test_first_attempt_success_reaches_review_without_any_retry(self) -> None:
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(), pool=self.pool, max_attempts=3,
        )
        self.assertEqual(final.state, MissionState.REVIEW)
        self.assertEqual(self._clone_count(), 1)
        kinds = [e.get("kind") for e in self._events() if e.get("mission_id") == self.mission.id]
        self.assertNotIn("retry.started", kinds)
        self.assertNotIn("recovery.decided", kinds)
        # `sandbox=` est fourni explicitement ici -> `provider.selected` ne
        # doit PAS être émis (réservé à l'auto-sélection) ; voir
        # TestProviderSelectedEventFiresOnAutoSelection ci-dessous.
        self.assertNotIn("provider.selected", kinds)


class TestProviderSelectedEventFiresOnAutoSelection(_RecoveryTestCase):
    def test_omitting_sandbox_with_pool_emits_provider_selected(self) -> None:
        final = launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, pool=self.pool, max_attempts=3,
        )
        self.assertEqual(final.state, MissionState.REVIEW)
        events = [e for e in self._events() if e.get("mission_id") == self.mission.id]
        selected = [e for e in events if e.get("kind") == "provider.selected"]
        self.assertEqual(len(selected), 1)
        self.assertIn("provider_name", selected[0])
        self.assertIn("reason", selected[0])


class TestProviderSelectedEventOnlyOnNewPath(_RecoveryTestCase):
    def test_historical_path_never_emits_provider_selected(self) -> None:
        """Preuve directe de la non-régression : le chemin `pool=None`
        n'émet JAMAIS ce nouvel événement — comportement historique
        rigoureusement inchangé."""
        launch_mission(
            self.mission.id, session_factory=self.factory, mission_service=self.mission_service,
            agent_runtime=self.runtime, sandbox=LinuxNamespaceSandbox(),
        )
        kinds = [e.get("kind") for e in self._events() if e.get("mission_id") == self.mission.id]
        self.assertNotIn("provider.selected", kinds)


class TestRotatePool(unittest.TestCase):
    def test_single_pool_returns_itself(self) -> None:
        p = a_pool("only")
        self.assertIs(_rotate_pool((p,), p), p)

    def test_rotates_to_the_next_real_pool_and_wraps_around(self) -> None:
        p1, p2, p3 = a_pool("p1"), a_pool("p2"), a_pool("p3")
        pools = (p1, p2, p3)
        self.assertEqual(_rotate_pool(pools, p1).pool_id, "p2")
        self.assertEqual(_rotate_pool(pools, p2).pool_id, "p3")
        self.assertEqual(_rotate_pool(pools, p3).pool_id, "p1")


if __name__ == "__main__":
    unittest.main()
