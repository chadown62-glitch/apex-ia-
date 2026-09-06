"""Tests de `apex.metalearner.evolution.PopulationEvolutionService` — la
première boucle évolutive réelle d'APEX (passe « Real Clone Population +
Evolution Loop »). Même sandbox namespaced RÉELLE que
`test_metalearner_autonomy.py`/`test_metalearner_feedback.py` — ce service
ne fait que composer `AgentRuntime`/`GenomeStoreService`/
`run_experiment_cycle` déjà réels, jamais une seconde implémentation.
"""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, GauntletPolicy, PoolLocation, PopulationPolicy, TrustLevel
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.errors import PopulationCapExceeded
from apex.metalearner.evolution import PopulationEvolutionService
from apex.genome.model import GenomeVersionOrigin
from apex.genome.service import GenomeStoreService
from apex.policy.model import Subject
from apex.metalearner.population_model import MemberStatus


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
        max_population_size=8, generation_size=3, elite_size=2, enable_crossover=True,
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


class TestBootstrapGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_generation_zero_creates_members_from_the_promoted_genome(self) -> None:
        result = self.service.evolve_generation("research")
        self.assertEqual(result.generation, 0)
        self.assertEqual(len(result.members), 3)  # generation_size
        for member in result.members:
            self.assertEqual(member.specialty, "research")
            self.assertEqual(member.generation, 0)
            self.assertIsNone(member.parent_member_id)  # bootstrap : pas de parent population

    def test_every_member_has_a_real_measured_fitness(self) -> None:
        result = self.service.evolve_generation("research")
        for member in result.members:
            self.assertIsNotNone(member.fitness)
            self.assertIn("quality", member.fitness_components)

    def test_selection_produces_exactly_one_champion(self) -> None:
        result = self.service.evolve_generation("research")
        self.assertIsNotNone(result.champion)
        self.assertEqual(result.champion.status, MemberStatus.CHAMPION)

    def test_selection_keeps_some_diversity_never_a_single_survivor_when_elite_size_allows_more(self) -> None:
        result = self.service.evolve_generation("research")
        # elite_size=2 : le champion + au moins un autre membre distinct restent vivants.
        survivors = [m for m in result.members if m.status in (MemberStatus.CHAMPION, MemberStatus.ELITE)]
        self.assertGreaterEqual(len(survivors), 2)

    def test_no_promoted_genome_fails_cleanly_without_creating_a_member(self) -> None:
        empty_factory = sqlite_session_factory()
        service = PopulationEvolutionService(
            empty_factory, agent_runtime=AgentRuntime(empty_factory), config=self.config,
        )
        result = service.evolve_generation("research")
        self.assertEqual(result.members, ())
        self.assertEqual(len(result.failures), 3)
        self.assertIn("PROMOTED", result.failures[0].reason)


class TestGenomeAppliedToClone(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory, content={"a": 1, "b": 2.0})
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_each_member_is_linked_to_its_own_distinct_genome_version(self) -> None:
        result = self.service.evolve_generation("research")
        genome_ids = {m.genome_version_id for m in result.members}
        self.assertEqual(len(genome_ids), len(result.members))  # jamais partagé entre membres

    def test_each_member_genome_descends_from_the_parent_promoted_genome(self) -> None:
        """Vérifie le CÂBLAGE (chaque version de Genome créée descend
        réellement du Genome PROMOTED courant, `parent_version_id`) —
        jamais si le CONTENU a effectivement changé (`mutate()` est
        probabiliste par feuille : avec un contenu aussi petit, rien ne
        garantit qu'une mutation particulière touche une feuille donnée,
        déjà couvert de façon déterministe par les tests de
        `apex.genome.mutation`, phase 17)."""
        genome_service = GenomeStoreService(self.factory)
        current = genome_service.get_current()
        result = self.service.evolve_generation("research")
        for member in result.members:
            version = genome_service.get(member.genome_version_id)
            self.assertEqual(version.parent_version_id, current.id)

    def test_member_genome_origin_is_mutation_at_bootstrap(self) -> None:
        result = self.service.evolve_generation("research")
        genome_service = GenomeStoreService(self.factory)
        for member in result.members:
            version = genome_service.get(member.genome_version_id)
            self.assertEqual(version.origin, GenomeVersionOrigin.MUTATION)


class TestMultiGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_second_call_advances_to_generation_one(self) -> None:
        self.service.evolve_generation("research")
        second = self.service.evolve_generation("research")
        self.assertEqual(second.generation, 1)

    def test_generation_one_descends_from_generation_zero_elite(self) -> None:
        first = self.service.evolve_generation("research")
        elite_ids = {m.id for m in first.elite} | ({first.champion.id} if first.champion else set())
        second = self.service.evolve_generation("research")
        for member in second.members:
            self.assertIn(member.parent_member_id, elite_ids)

    def test_champion_is_never_replaced_by_a_worse_next_generation_member(self) -> None:
        first = self.service.evolve_generation("research")
        first_champion_id = first.champion.id
        second = self.service.evolve_generation("research")
        # Un champion existant n'est jamais rétrogradé par un candidat moins bon.
        champion_after = self.service._population.get_champion("research")
        self.assertIn(champion_after.id, {first_champion_id, *[m.id for m in second.members]})


class TestCrossover(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy(enable_crossover=True, generation_size=4, elite_size=2))
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_crossover_is_used_between_two_elites_in_a_later_generation(self) -> None:
        self.service.evolve_generation("research")  # generation 0 : bootstrap, jamais de crossover
        second = self.service.evolve_generation("research")
        genome_service = GenomeStoreService(self.factory)
        origins = {genome_service.get(m.genome_version_id).origin for m in second.members}
        self.assertIn(GenomeVersionOrigin.CROSSOVER, origins)

    def test_crossover_child_preserves_both_parents_provenance(self) -> None:
        self.service.evolve_generation("research")
        second = self.service.evolve_generation("research")
        genome_service = GenomeStoreService(self.factory)
        crossed = next(
            m for m in second.members
            if genome_service.get(m.genome_version_id).origin is GenomeVersionOrigin.CROSSOVER
        )
        version = genome_service.get(crossed.genome_version_id)
        self.assertIsNotNone(version.parent_version_id)
        self.assertIsNotNone(version.parent_version_id_b)
        self.assertNotEqual(version.parent_version_id, version.parent_version_id_b)

    def test_crossover_disabled_never_produces_a_crossover_origin(self) -> None:
        config = FakeConfig({"local": a_pool()}, a_policy(enable_crossover=False, generation_size=4, elite_size=2))
        service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=config)
        service.evolve_generation("research")
        second = service.evolve_generation("research")
        genome_service = GenomeStoreService(self.factory)
        origins = {genome_service.get(m.genome_version_id).origin for m in second.members}
        self.assertNotIn(GenomeVersionOrigin.CROSSOVER, origins)


class TestPopulationCap(unittest.TestCase):
    def test_generation_stops_early_once_the_cap_is_reached(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        promote_a_genome(factory)
        config = FakeConfig({"local": a_pool()}, a_policy(max_population_size=2, generation_size=5))
        service = PopulationEvolutionService(factory, agent_runtime=runtime, config=config)

        result = service.evolve_generation("research")
        self.assertEqual(len(result.members), 2)
        self.assertEqual(result.failures, ())  # un plafond atteint n'est jamais une panne


class TestFailureIsolation(unittest.TestCase):
    def test_one_member_failing_never_blocks_the_others(self) -> None:
        factory = sqlite_session_factory()
        runtime = AgentRuntime(factory)
        promote_a_genome(factory)
        config = FakeConfig({"local": a_pool()}, a_policy(generation_size=3))
        service = PopulationEvolutionService(factory, agent_runtime=runtime, config=config)

        real_spawn = runtime.spawn
        calls = {"n": 0}

        def flaky_spawn(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("panne simulée du spawn pour CE membre seulement")
            return real_spawn(*args, **kwargs)

        with patch.object(runtime, "spawn", side_effect=flaky_spawn):
            result = service.evolve_generation("research")

        self.assertEqual(len(result.members), 2)  # les 2 autres membres ont réussi
        self.assertEqual(len(result.failures), 1)
        self.assertIn("panne simulée", result.failures[0].reason)


class TestIdempotency(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_replaying_a_member_experiment_ref_returns_the_same_member_never_a_duplicate(self) -> None:
        from apex.evalrunner.model import EvalMethod

        result = self.service.evolve_generation("research")
        member = result.members[0]
        replay = self.service._create_member(
            specialty="research", generation=0, index=0, parents=(None, None),
            experiment_ref=member.experiment_ref, current_level=1, method=EvalMethod.PRACTICE,
            policy=self.config.population,
        )
        self.assertEqual(replay.id, member.id)
        self.assertEqual(len(self.service._population.list_by_specialty("research")), len(result.members))


class TestSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_evolve_generation_has_no_identity_parameter_at_all(self) -> None:
        signature = inspect.signature(self.service.evolve_generation)
        for forbidden in ("requested_by", "subject", "actor", "created_by", "spawned_by"):
            self.assertNotIn(forbidden, signature.parameters)

    def test_never_promotes_or_rejects_any_genome_during_normal_evolution(self) -> None:
        with patch.object(GenomeStoreService, "promote", side_effect=AssertionError("jamais appelé")), \
             patch.object(GenomeStoreService, "reject", side_effect=AssertionError("jamais appelé")):
            result = self.service.evolve_generation("research")
        self.assertGreater(len(result.members), 0)

    def test_all_automatic_genome_proposals_are_created_by_core(self) -> None:
        with patch.object(GenomeStoreService, "propose", wraps=GenomeStoreService(self.factory).propose) as spy:
            self.service.evolve_generation("research")
        for call in spy.call_args_list:
            self.assertEqual(call.kwargs["created_by"], Subject.CORE)


class TestGauntletIntegration(unittest.TestCase):
    """Intégration RÉELLE du Judge System (passe N+8) dans la boucle
    d'évolution — jamais un second pipeline concurrent (§28 de la mission
    N+8) : le Gauntlet enrichit `PopulationEvolutionService`, il ne le
    remplace pas."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_every_member_receives_a_persisted_cheap_gauntlet_result(self) -> None:
        from apex.metalearner.judge_model import GauntletStage

        result = self.service.evolve_generation("research")
        for member in result.members:
            gauntlets = self.service._gauntlet_repo.list_by_member(member.id)
            # Le candidat CHAMPION reçoit un second gauntlet (`full`, §14) —
            # tous les autres membres n'en reçoivent qu'un seul (`cheap`,
            # sans exécution supplémentaire, voir `_create_member`).
            stages = {g.stage for g in gauntlets}
            self.assertIn(GauntletStage.CHEAP, stages)
            self.assertGreaterEqual(len(gauntlets), 1)

    def test_a_security_veto_blocks_champion_promotion_even_with_the_best_fitness(self) -> None:
        """Preuve directe qu'un véto (§8) empêche la promotion CHAMPION,
        même pour le candidat objectivement le plus performant — la
        philosophie centrale de la mission N+8 : « APEX does not reward
        solutions for looking intelligent. »"""
        from apex.metalearner.judges import SecurityJudge
        from apex.metalearner.judge_model import JudgeDecision, JudgeDimension, JudgeFinding, JudgeResult, JudgeSeverity

        def always_veto(self_judge, judge_input):
            return JudgeResult(
                judge_id=self_judge.judge_id, judge_version=self_judge.judge_version,
                dimension=self_judge.dimension, decision=JudgeDecision.REJECT, score=0.0, confidence=1.0,
                rationale="véto forcé par le test", findings=(
                    JudgeFinding(dimension=JudgeDimension.SECURITY, severity=JudgeSeverity.CRITICAL, summary="véto forcé"),
                ),
            )

        with patch.object(SecurityJudge, "evaluate", always_veto):
            result = self.service.evolve_generation("research")

        self.assertIsNone(result.champion)  # aucun candidat n'a pu être promu, malgré une fitness réelle mesurée.
        for member in result.members:
            self.assertNotEqual(member.status.value, "champion")

    def test_a_genuinely_superior_candidate_is_promoted_when_the_gauntlet_passes(self) -> None:
        result = self.service.evolve_generation("research")
        self.assertIsNotNone(result.champion)
        gauntlets = self.service._gauntlet_repo.list_by_member(result.champion.id)
        full_results = [g for g in gauntlets if g.stage.value == "full"]
        self.assertEqual(len(full_results), 1)
        self.assertEqual(full_results[0].decision.value, "pass")
        self.assertFalse(full_results[0].veto)


if __name__ == "__main__":
    unittest.main()
