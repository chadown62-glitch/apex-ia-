"""Tests de `apex.metalearner.learning_orchestrator.LearningOrchestrator` —
passe « Autonomous Multi-Generation Learning Orchestrator ». Même sandbox
SQLite en mémoire que `tests/test_evolution_service.py` : l'orchestrateur ne
fait QUE composer `PopulationEvolutionService.evolve_generation` déjà réel
(aucune génération/fitness/sélection simulée ici) et `LearningSessionService`
(bookkeeping pur, jamais un second moteur d'évolution).
"""

from __future__ import annotations

import threading
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, GauntletPolicy, PoolLocation, PopulationPolicy, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import LearningSessionAlreadyRunning
from apex.genome.service import GenomeStoreService
from apex.metalearner.learning_orchestrator import LearningOrchestrator
from apex.metalearner.learning_session_model import LearningSessionStatus
from apex.policy.model import Subject
from tests import db_test_support as support


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
    def __init__(
        self, pools: dict[str, ComputePool], population: PopulationPolicy, gauntlet: GauntletPolicy | None = None,
    ) -> None:
        self.pools = pools
        self.population = population
        self.gauntlet = gauntlet or a_gauntlet_policy()


def a_policy(**overrides) -> PopulationPolicy:
    defaults = dict(
        max_population_size=8, generation_size=2, elite_size=2, enable_crossover=False,
        max_session_generations=20,
    )
    defaults.update(overrides)
    return PopulationPolicy(**defaults)


def a_gauntlet_policy(**overrides) -> GauntletPolicy:
    defaults = dict(
        min_confidence_threshold=0.6, efficiency_reject_threshold=0.9,
        major_regression_factor=2.0, enable_expensive_judges=True,
    )
    defaults.update(overrides)
    return GauntletPolicy(**defaults)


def promote_a_genome(factory, content=None) -> str:
    genome = GenomeStoreService(factory)
    genome.propose(content=content or {"a": 1, "b": 2.0}, created_by=Subject.HUMAN)
    proposed = genome.list_all()[0]
    genome.promote(proposed.id, requested_by=Subject.HUMAN)
    return proposed.id


def _wait_terminal(orch: LearningOrchestrator, session_id: str, *, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    session = orch.get_session(session_id)
    while session is not None and not session.status.is_terminal and time.monotonic() < deadline:
        time.sleep(0.02)
        session = orch.get_session(session_id)
    return session


class OrchestratorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.orch = LearningOrchestrator(self.factory, agent_runtime=self.runtime, config=self.config)


class TestBasicMultiGeneration(OrchestratorTestBase):
    def test_runs_until_max_generations_reached_and_completes(self) -> None:
        session = self.orch.start_session("research", max_generations=3)
        final = _wait_terminal(self.orch, session.id)
        self.assertIsNotNone(final)
        self.assertEqual(final.status, LearningSessionStatus.COMPLETED)
        self.assertEqual(final.stop_reason, "max_generations_reached")
        self.assertEqual(final.generations_completed, 3)

    def test_best_fitness_and_champion_are_populated(self) -> None:
        session = self.orch.start_session("research", max_generations=2)
        final = _wait_terminal(self.orch, session.id)
        self.assertIsNotNone(final.best_fitness)
        self.assertIsNotNone(final.best_member_id)
        self.assertIsNotNone(final.best_genome_version_id)

    def test_generation_loop_actually_advances_population_generations(self) -> None:
        from apex.metalearner.population_service import PopulationService

        session = self.orch.start_session("research", max_generations=3)
        _wait_terminal(self.orch, session.id)
        pop = PopulationService(self.factory)
        members = pop.list_by_specialty("research")
        self.assertEqual(max(m.generation for m in members), 2)  # générations 0,1,2


class TestPersistence(OrchestratorTestBase):
    def test_session_is_durably_readable_via_get_session(self) -> None:
        session = self.orch.start_session("research", max_generations=1)
        final = _wait_terminal(self.orch, session.id)
        reread = self.orch.get_session(session.id)
        self.assertEqual(reread.id, final.id)
        self.assertEqual(reread.status, final.status)
        self.assertEqual(reread.generations_completed, final.generations_completed)

    def test_list_sessions_by_specialty_returns_it(self) -> None:
        session = self.orch.start_session("research", max_generations=1)
        _wait_terminal(self.orch, session.id)
        sessions = self.orch.list_sessions(specialty="research")
        self.assertIn(session.id, [s.id for s in sessions])


class TestResume(OrchestratorTestBase):
    def test_resume_after_failure_continues_from_the_next_generation_never_from_zero(self) -> None:
        from apex.metalearner.population_service import PopulationService

        empty_factory = sqlite_session_factory()  # aucun genome PROMOTED -> échec garanti
        runtime = AgentRuntime(empty_factory)
        orch = LearningOrchestrator(empty_factory, agent_runtime=runtime, config=self.config)

        session = orch.start_session("research", max_generations=3)
        failed = _wait_terminal(orch, session.id)
        self.assertEqual(failed.status, LearningSessionStatus.FAILED)
        self.assertEqual(failed.stop_reason, "generation_failed_all_members")
        self.assertEqual(failed.generations_completed, 0)

        promote_a_genome(empty_factory)  # corrige la panne d'infrastructure
        resumed = orch.resume_session(session.id)
        final = _wait_terminal(orch, resumed.id)
        self.assertEqual(final.status, LearningSessionStatus.COMPLETED)
        self.assertEqual(final.generations_completed, 3)

        pop = PopulationService(empty_factory)
        members = pop.list_by_specialty("research")
        generations = sorted({m.generation for m in members})
        self.assertEqual(generations, [0, 1, 2])  # aucun trou, aucun doublon

    def test_resuming_a_running_session_raises_lookup_or_value_error(self) -> None:
        session = self.orch.start_session("research", max_generations=5)
        with self.assertRaises(ValueError):
            self.orch.resume_session(session.id)
        # Attend la fin réelle du thread de fond avant de rendre la main —
        # sans ça, une session RUNNING abandonnée continuerait à consommer
        # des ressources sandbox réelles en arrière-plan pendant TOUT le
        # reste de la suite de tests (thread démon, jamais tué par la fin
        # de CE test), une source de flakiness pour des tests sans rapport.
        _wait_terminal(self.orch, session.id)

    def test_resuming_an_unknown_session_raises_lookup_error(self) -> None:
        with self.assertRaises(LookupError):
            self.orch.resume_session("not-a-real-session-id")


class TestFailureHandling(OrchestratorTestBase):
    def test_no_promoted_genome_fails_safely_never_raises_out_of_the_loop(self) -> None:
        empty_factory = sqlite_session_factory()
        runtime = AgentRuntime(empty_factory)
        orch = LearningOrchestrator(empty_factory, agent_runtime=runtime, config=self.config)

        session = orch.start_session("research", max_generations=3)
        final = _wait_terminal(orch, session.id)
        self.assertEqual(final.status, LearningSessionStatus.FAILED)
        self.assertEqual(final.stop_reason, "generation_failed_all_members")
        self.assertIsNotNone(final.error)

    def test_evolve_generation_raising_is_captured_as_a_failed_session(self) -> None:
        from unittest.mock import patch

        # Le mock est installé AVANT le démarrage — une seule session à la
        # fois dans ce test (jamais deux sessions réelles concurrentes sur
        # la même connexion SQLite en mémoire, non sûr pour du vrai
        # parallélisme — voir `TestIdempotencyAndConcurrency` pour la
        # concurrence réelle, testée contre PostgreSQL).
        with patch.object(
            self.orch._evolution, "evolve_generation", side_effect=RuntimeError("panne simulée"),
        ):
            session = self.orch.start_session("research", max_generations=3)
            final = _wait_terminal(self.orch, session.id)
        self.assertEqual(final.status, LearningSessionStatus.FAILED)
        self.assertEqual(final.stop_reason, "generation_raised")
        self.assertIn("panne simulée", final.error)


class TestCancellation(OrchestratorTestBase):
    def test_cancel_requested_between_generations_stops_the_session(self) -> None:
        session = self.orch.start_session("research", max_generations=20)
        time.sleep(0.05)  # laisse au moins une génération se terminer
        cancelled = self.orch.cancel_session(session.id)
        self.assertIn(
            cancelled.status, (LearningSessionStatus.CANCEL_REQUESTED, LearningSessionStatus.CANCELLED),
        )
        final = _wait_terminal(self.orch, session.id)
        self.assertEqual(final.status, LearningSessionStatus.CANCELLED)
        self.assertEqual(final.stop_reason, "cancelled")

    def test_cancelling_an_already_terminal_session_is_a_noop(self) -> None:
        session = self.orch.start_session("research", max_generations=1)
        _wait_terminal(self.orch, session.id)
        result = self.orch.cancel_session(session.id)
        self.assertEqual(result.status, LearningSessionStatus.COMPLETED)  # inchangé, jamais une erreur

    def test_generations_completed_reflects_real_population_state_even_when_cancelled_mid_race(self) -> None:
        from apex.metalearner.population_service import PopulationService

        session = self.orch.start_session("research", max_generations=20)
        time.sleep(0.05)
        self.orch.cancel_session(session.id)
        final = _wait_terminal(self.orch, session.id)
        pop = PopulationService(self.factory)
        members = pop.list_by_specialty("research")
        expected_generations = len({m.generation for m in members})
        self.assertEqual(final.generations_completed, expected_generations)


class TestIdempotencyAndConcurrency(OrchestratorTestBase):
    def test_starting_the_same_specialty_twice_raises_already_running(self) -> None:
        """Bloque délibérément la première génération (`evolve_generation`
        patché pour attendre un `Event`) — sans ça, avec un plafond de
        population aussi petit que `a_policy()` par défaut, une session
        pourrait légitimement atteindre `population_at_capacity` (COMPLETED)
        avant même que ce test n'ait la chance d'appeler `start_session`
        une seconde fois, rendant ce test intrinsèquement instable plutôt
        que de prouver quoi que ce soit sur l'exclusion mutuelle elle-même."""
        from unittest.mock import patch

        release = threading.Event()
        real_evolve = self.orch._evolution.evolve_generation

        def blocked(*args, **kwargs):
            release.wait(timeout=30.0)
            return real_evolve(*args, **kwargs)

        with patch.object(self.orch._evolution, "evolve_generation", side_effect=blocked):
            session = self.orch.start_session("research", max_generations=20)
            with self.assertRaises(LearningSessionAlreadyRunning):
                self.orch.start_session("research", max_generations=20)
        release.set()
        _wait_terminal(self.orch, session.id)

    def test_resuming_an_already_resumed_session_a_second_time_is_rejected(self) -> None:
        """Seule la PREMIÈRE reprise réussit ; une seconde reprise de la
        MÊME session, désormais `running`, n'est jamais silencieusement
        acceptée (`resume` n'est valide que depuis `failed`/`cancelled`) —
        preuve séquentielle de l'idempotence ; la preuve VRAIMENT
        concurrente (plusieurs connexions réelles simultanées) est dans
        `TestConcurrencyWithRealDatabase` ci-dessous (PostgreSQL réel :
        SQLite ne partage qu'UNE seule connexion sous-jacente, même avec
        `check_same_thread=False` — une vraie concurrence multi-thread y
        est intrinsèquement instable, indépendamment de la logique testée)."""
        running = self.orch.start_session("research", max_generations=20)
        time.sleep(0.05)
        self.orch.cancel_session(running.id)
        cancelled = _wait_terminal(self.orch, running.id)
        self.assertEqual(cancelled.status, LearningSessionStatus.CANCELLED)

        resumed = self.orch.resume_session(cancelled.id)
        self.assertEqual(resumed.status, LearningSessionStatus.RUNNING)
        with self.assertRaises(ValueError):
            self.orch.resume_session(cancelled.id)
        _wait_terminal(self.orch, resumed.id)


_SKIP_REASON = (
    "PostgreSQL réel non disponible ou identifiants absents — "
    "voir docs/PHASE_2_REPORT.md, section validation PostgreSQL"
)


@unittest.skipUnless(support.postgres_testable(), _SKIP_REASON)
class TestConcurrencyWithRealDatabase(unittest.TestCase):
    """Preuve VRAIMENT concurrente de l'exclusion mutuelle inter-processus
    (§9 de la mission : « doit fonctionner correctement à travers PLUSIEURS
    processus/workers API ») — chaque thread construit sa PROPRE connexion
    PostgreSQL indépendante (jamais partagée), simulant fidèlement plusieurs
    workers API distincts qui contendent pour le même index UNIQUE partiel."""

    def setUp(self) -> None:
        import os

        support.recreate_test_database()
        support.run_upgrade()
        os.environ["POSTGRES_DB"] = support.TEST_DB_NAME
        self._promote_a_genome()

    def tearDown(self) -> None:
        support.drop_test_database()
        support.drop_application_roles()

    def _session_factory(self):
        from apex.config.loader import load_config
        from apex.db.engine import build_engine, build_session_factory, database_url_from_config

        config = load_config()
        return build_session_factory(build_engine(database_url_from_config(config)))

    def _promote_a_genome(self) -> None:
        genome = GenomeStoreService(self._session_factory())
        genome.propose(content={"x": 1}, created_by=Subject.HUMAN)
        proposed = genome.list_all()[0]
        genome.promote(proposed.id, requested_by=Subject.HUMAN)

    def _make_orchestrator(self) -> LearningOrchestrator:
        factory = self._session_factory()
        return LearningOrchestrator(
            factory, agent_runtime=AgentRuntime(factory), config=FakeConfig({"local": a_pool()}, a_policy()),
        )

    def test_concurrent_start_from_independent_connections_only_one_wins(self) -> None:
        results: list[object] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(5)

        def attempt() -> None:
            orch = self._make_orchestrator()
            barrier.wait()
            try:
                results.append(orch.start_session("research", max_generations=20))
            except LearningSessionAlreadyRunning as exc:
                errors.append(exc)

        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 4)
        _wait_terminal(self._make_orchestrator(), results[0].id, timeout_s=20.0)

    def test_concurrent_resume_from_independent_connections_only_one_wins(self) -> None:
        setup_orch = self._make_orchestrator()
        running = setup_orch.start_session("research", max_generations=20)
        time.sleep(0.1)
        setup_orch.cancel_session(running.id)
        cancelled = _wait_terminal(setup_orch, running.id, timeout_s=20.0)
        self.assertEqual(cancelled.status, LearningSessionStatus.CANCELLED)

        results: list[object] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(3)

        def attempt() -> None:
            orch = self._make_orchestrator()
            barrier.wait()
            try:
                results.append(orch.resume_session(cancelled.id))
            except (LearningSessionAlreadyRunning, ValueError) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=attempt) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 2)
        _wait_terminal(self._make_orchestrator(), results[0].id, timeout_s=20.0)


class TestSecurity(OrchestratorTestBase):
    def test_start_session_has_no_identity_parameter(self) -> None:
        import inspect

        signature = inspect.signature(self.orch.start_session)
        for forbidden in ("subject", "requested_by", "actor", "created_by"):
            self.assertNotIn(forbidden, signature.parameters)

    def test_start_session_always_records_created_by_human(self) -> None:
        session = self.orch.start_session("research", max_generations=1)
        self.assertEqual(session.created_by, Subject.HUMAN.value)
        _wait_terminal(self.orch, session.id)

    def test_caller_cannot_request_more_generations_than_the_administrator_ceiling(self) -> None:
        config = FakeConfig({"local": a_pool()}, a_policy(max_session_generations=2))
        orch = LearningOrchestrator(self.factory, agent_runtime=self.runtime, config=config)
        with self.assertRaises(ValueError):
            orch.start_session("research", max_generations=3)

    def test_zero_or_negative_max_generations_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.orch.start_session("research", max_generations=0)


class TestRealIntegration(OrchestratorTestBase):
    """Chaîne complète RÉELLE — orchestrateur -> population réelle -> clone
    réel (`AgentRuntime.spawn`) -> cycle d'expérience réel
    (`run_experiment_cycle`) -> fitness réelle -> génération suivante — sans
    aucune simulation/mock, même infra sandbox que `test_evolution_service.py`."""

    def test_three_generations_produce_real_measured_fitness_and_lineage(self) -> None:
        from apex.metalearner.population_model import MemberStatus
        from apex.metalearner.population_service import PopulationService

        session = self.orch.start_session("research", max_generations=3)
        final = _wait_terminal(self.orch, session.id, timeout_s=20.0)
        self.assertEqual(final.status, LearningSessionStatus.COMPLETED)

        pop = PopulationService(self.factory)
        members = pop.list_by_specialty("research")
        self.assertGreater(len(members), 0)
        for member in members:
            self.assertIsNotNone(member.fitness)
            self.assertIn("quality", member.fitness_components)

        champion = pop.get_champion("research")
        self.assertIsNotNone(champion)
        self.assertEqual(champion.status, MemberStatus.CHAMPION)
        self.assertEqual(final.best_member_id, champion.id)
        self.assertAlmostEqual(final.best_fitness, champion.fitness)

        gen1_plus = [m for m in members if m.generation > 0]
        for member in gen1_plus:
            self.assertIsNotNone(member.parent_member_id)  # lignage réel, jamais orphelin


if __name__ == "__main__":
    unittest.main()
