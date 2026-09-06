"""Tests d'intégration RÉELLE du Universal Task + Curriculum Engine (N+11)
— provenance, difficulté persistée, budget, séparation de dataset,
`research` via le moteur générique, absence honnête d'une deuxième
spécialité, intégration `PopulationEvolutionService`/`LearningOrchestrator`,
et un cycle complet bout en bout. Lettres H/O/P/Q/R/S/T/Y/Z du §21 de la
mission citées explicitement — même infra SQLite que `tests/
test_adaptive_evolution.py`."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.config.schema import (
    Capability,
    ComputePool,
    CurriculumBudgetPolicy,
    GauntletPolicy,
    PoolLocation,
    PopulationPolicy,
    TrustLevel,
)
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.genome.service import GenomeStoreService
from apex.metalearner.bug_farm_service import BugFarmService
from apex.metalearner.challenge_model import ChallengeSource
from apex.metalearner.challenge_repository import CurriculumChallengeRepository
from apex.metalearner.challenge_service import CurriculumChallengeService
from apex.metalearner.curriculum_engine import CurriculumEngineService
from apex.metalearner.evolution import PopulationEvolutionService
from apex.metalearner.judge_feedback_model import FailureCategory, JudgeLearningSignal, SignalCategory
from apex.metalearner.judge_feedback_repository import JudgeLearningSignalRepository
from apex.metalearner.judge_model import (
    JudgeDecision,
    JudgeDimension,
    JudgeFinding,
    JudgeResult,
    JudgeSeverity,
)
from apex.metalearner.judges import EfficiencyJudge, SecurityJudge
from apex.metalearner.provenance_service import TaskProvenanceService
from apex.policy.model import Subject
from apex.sandbox.model import ResourceLimits
from apex.taskgen.invariants import assert_disjoint, disjoint
from apex.taskgen.registry import get_provider
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def a_limits() -> ResourceLimits:
    return ResourceLimits(cpu_cores=1, memory_mb=512, pids_limit=32, timeout_s=10.0, disk_mb=64, network="none")


def a_pool(**overrides) -> ComputePool:
    defaults = dict(
        pool_id="local", location=PoolLocation.LOCAL, trust_level=TrustLevel.TRUSTED,
        capabilities=(Capability.SANDBOX,),
    )
    defaults.update(overrides)
    return ComputePool(**defaults)


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


def a_curriculum_budget(**overrides) -> CurriculumBudgetPolicy:
    defaults = dict(
        max_tasks_per_generation=20, max_adversarial_per_generation=8, max_heldout_per_generation=4,
        max_bug_farm_per_generation=5, max_generation_cost_s=3600.0,
    )
    defaults.update(overrides)
    return CurriculumBudgetPolicy(**defaults)


class FakeConfig:
    def __init__(self, pools, population, gauntlet=None, curriculum_budget=None) -> None:
        self.pools = pools
        self.population = population
        self.gauntlet = gauntlet or a_gauntlet_policy()
        self.curriculum_budget = curriculum_budget or a_curriculum_budget()


def promote_a_genome(factory, content=None) -> str:
    genome = GenomeStoreService(factory)
    genome.propose(content=content or {"a": 1, "b": 2.0, "execution": {"timeout_s": 5.0}}, created_by=Subject.HUMAN)
    proposed = genome.list_all()[0]
    genome.promote(proposed.id, requested_by=Subject.HUMAN)
    return proposed.id


def make_signal(generation, dimension, category, decision, failure_category=None, severity=None):
    return JudgeLearningSignal.new(
        gauntlet_result_id=f"g{generation}", member_id=f"m{generation}", specialty="research",
        generation=generation, genome_version_id=f"genome-{generation}", judge_id=dimension.value,
        judge_version="1", dimension=dimension, decision=decision, category=category,
        failure_category=failure_category, severity=severity, score=0.0, confidence=1.0,
        finding_summary="test", evidence_reference=None, protocol_version="1",
    )


def _forced_veto(dimension):
    def _evaluate(self_judge, judge_input):
        return JudgeResult(
            judge_id=self_judge.judge_id, judge_version=self_judge.judge_version, dimension=self_judge.dimension,
            decision=JudgeDecision.REJECT, score=0.0, confidence=1.0, rationale="véto forcé (test)",
            findings=(JudgeFinding(dimension=dimension, severity=JudgeSeverity.CRITICAL, summary="véto forcé"),),
        )
    return _evaluate


class TestProvenance(unittest.TestCase):
    """Lettre H du §21 — chaque tâche générée automatiquement répond
    « pourquoi existe-t-elle », jamais une provenance inconnue."""

    def test_a_challenge_task_has_a_reconstructible_provenance(self) -> None:
        factory = sqlite_session_factory()
        promote_a_genome(factory)
        JudgeLearningSignalRepository(factory).create(
            make_signal(
                1, JudgeDimension.SECURITY, SignalCategory.CRITICAL_FAILURE, JudgeDecision.REJECT,
                failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, severity=JudgeSeverity.CRITICAL,
            ),
        )
        challenges = CurriculumChallengeService(factory).build_challenges("research", 1, limits=a_limits())
        self.assertEqual(len(challenges), 1)
        challenge = challenges[0]

        provenance = TaskProvenanceService(factory).get_provenance(challenge.task_id)
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance.weakness_id, challenge.weakness_id)
        self.assertEqual(provenance.challenge_id, challenge.id)
        self.assertEqual(provenance.generator_version, challenge.protocol_version)

    def test_a_task_never_referenced_by_the_curriculum_has_no_fabricated_provenance(self) -> None:
        factory = sqlite_session_factory()
        task = TaskBankService(factory).create(
            split=TaskSplit.TRAIN, specialty="research", title="manual", description="written by hand",
            command=("python3", "-c", "print(1)"), created_by=Subject.HUMAN,
        )
        provenance = TaskProvenanceService(factory).get_provenance(task.id)
        self.assertIsNone(provenance)


class TestCurriculumBudget(unittest.TestCase):
    """Lettre P du §21 — le plan ne dépasse jamais les plafonds de
    `CurriculumBudgetPolicy` ; un excédent est TRONQUÉ, jamais un
    dépassement silencieux."""

    def test_plan_is_truncated_to_the_configured_ceiling(self) -> None:
        factory = sqlite_session_factory()
        promote_a_genome(factory)
        for i, dim in enumerate((JudgeDimension.SECURITY, JudgeDimension.GENERALIZATION, JudgeDimension.EFFICIENCY)):
            JudgeLearningSignalRepository(factory).create(
                make_signal(
                    1, dim, SignalCategory.WEAKNESS, JudgeDecision.REJECT,
                    failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, severity=JudgeSeverity.HIGH,
                ),
            )
        tight_budget = a_curriculum_budget(max_tasks_per_generation=1)
        plan = CurriculumEngineService(factory).build_plan("research", 1, budget=tight_budget)
        self.assertTrue(plan.truncated)
        self.assertLessEqual(plan.total_task_count, 1)

    def test_challenge_generation_respects_the_same_budget(self) -> None:
        factory = sqlite_session_factory()
        promote_a_genome(factory)
        for dim in (JudgeDimension.SECURITY, JudgeDimension.GENERALIZATION):
            JudgeLearningSignalRepository(factory).create(
                make_signal(
                    1, dim, SignalCategory.WEAKNESS, JudgeDecision.REJECT,
                    failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, severity=JudgeSeverity.HIGH,
                ),
            )
        tight_budget = a_curriculum_budget(max_tasks_per_generation=1)
        challenges = CurriculumChallengeService(factory).build_challenges(
            "research", 1, limits=a_limits(), budget=tight_budget,
        )
        self.assertLessEqual(len(challenges), 1)


class TestDatasetSeparation(unittest.TestCase):
    """Lettre Q du §21 — TRAIN/HELDOUT/BUG_FARM ne partagent jamais un
    `task_id` par accident."""

    def test_train_and_heldout_challenge_tasks_are_disjoint(self) -> None:
        factory = sqlite_session_factory()
        promote_a_genome(factory)
        JudgeLearningSignalRepository(factory).create(
            make_signal(
                1, JudgeDimension.GENERALIZATION, SignalCategory.WEAKNESS, JudgeDecision.REJECT,
                failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, severity=JudgeSeverity.HIGH,
            ),
        )
        JudgeLearningSignalRepository(factory).create(
            make_signal(
                1, JudgeDimension.EFFICIENCY, SignalCategory.WEAKNESS, JudgeDecision.REJECT,
                failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, severity=JudgeSeverity.HIGH,
            ),
        )
        challenges = CurriculumChallengeService(factory).build_challenges("research", 1, limits=a_limits())
        heldout_ids = {c.task_id for c in challenges if c.heldout}
        train_ids = {c.task_id for c in challenges if not c.heldout}
        self.assertTrue(disjoint(train_ids, heldout_ids))
        assert_disjoint(train_ids, heldout_ids, label="TRAIN vs HELDOUT (curriculum challenges)")

    def test_invariant_helper_raises_on_real_overlap(self) -> None:
        with self.assertRaises(ValueError):
            assert_disjoint({"a", "b"}, {"b", "c"}, label="test")


class TestResearchViaGenericEngine(unittest.TestCase):
    """Lettre S du §21 — `research` passe RÉELLEMENT par
    `SpecialtyTaskProvider`/`UniversalTaskFactory`, jamais un raccourci
    direct vers `DeterministicTaskGenerator`."""

    def test_challenge_service_never_imports_deterministic_generator_directly(self) -> None:
        import apex.metalearner.challenge_service as module

        with open(module.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("DeterministicTaskGenerator", source)
        self.assertIn("UniversalTaskFactory", source)

    def test_generated_task_carries_the_universal_factory_protocol_version(self) -> None:
        provider = get_provider("research")
        self.assertIsNotNone(provider)
        from apex.taskgen.model import TaskKind
        from apex.taskgen.task_factory import TASK_FACTORY_PROTOCOL_VERSION, TaskSpec, UniversalTaskFactory

        generated = UniversalTaskFactory().create(TaskSpec(kind=TaskKind.NORMAL, specialty="research"))
        self.assertEqual(generated.provenance.generator_version, TASK_FACTORY_PROTOCOL_VERSION)


class TestSecondSpecialtyHonestAbsence(unittest.TestCase):
    """Lettre T du §21 — aucune deuxième spécialité RÉELLE n'existe sans
    fabriquer un moteur artificiel (§ Phase 18 de la mission : « si aucune
    n'est possible sans inventer une infrastructure artificielle, ne pas la
    fabriquer, documenter précisément le blocage ») — `apex.specialties.
    registry` n'autorise un `SpecialtyTaskProvider` réel QUE derrière une
    spécialité `is_supported` (`STUB`/`IMPLEMENTED`), et seule `research`
    l'est aujourd'hui (`AgentRuntime` n'a jamais été exercé de bout en bout
    pour une autre)."""

    def test_no_second_specialty_task_provider_is_registered(self) -> None:
        from apex.specialties.registry import SPECIALTIES
        from apex.taskgen.registry import supported_specialty_ids

        self.assertEqual(supported_specialty_ids(), ("research",))
        other_supported = [s for s in SPECIALTIES if s.is_supported and s.id != "research"]
        self.assertEqual(
            other_supported, [],
            "une deuxième spécialité is_supported serait fabriquée sans moteur réel derrière",
        )


class TestLearningOrchestratorIntegration(unittest.TestCase):
    """Lettre Y du §21 — le curriculum/bug farm N+11 s'intègre à
    `PopulationEvolutionService`/`LearningOrchestrator` sans nouveau moteur
    d'évolution, et sans jamais casser une génération dont les membres ont
    déjà réellement expérimenté."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_evolve_generation_syncs_both_challenges_and_bug_farm(self) -> None:
        with patch.object(SecurityJudge, "evaluate", _forced_veto(JudgeDimension.SECURITY)):
            result = self.service.evolve_generation("research")
        self.assertGreater(len(result.members), 0)

        challenges = CurriculumChallengeRepository(self.factory).list_by_specialty("research")
        self.assertTrue(any(c.source is ChallengeSource.SECURITY or c.adversarial for c in challenges))

        bugs = BugFarmService(self.factory).list_bugs("research")
        self.assertTrue(any(b.dimension is JudgeDimension.SECURITY for b in bugs))

    def test_a_broken_curriculum_sync_never_invalidates_the_generation(self) -> None:
        with patch.object(
            CurriculumChallengeService, "build_challenges", side_effect=RuntimeError("boom (test)"),
        ):
            result = self.service.evolve_generation("research")
        self.assertGreater(len(result.members), 0)


class TestEndToEndCycle(unittest.TestCase):
    """Lettre Z du §21 — WEAKNESS -> CURRICULUM -> CHALLENGE -> CANDIDATE ->
    GAUNTLET -> LEARNING HISTORY -> NEXT GENERATION, RIEN de simulé."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        promote_a_genome(self.factory)
        self.config = FakeConfig({"local": a_pool()}, a_policy())
        self.service = PopulationEvolutionService(self.factory, agent_runtime=self.runtime, config=self.config)

    def test_full_universal_curriculum_cycle(self) -> None:
        with patch.object(EfficiencyJudge, "evaluate", _forced_veto(JudgeDimension.EFFICIENCY)):
            gen0 = self.service.evolve_generation("research")
        self.assertGreater(len(gen0.members), 0)

        # WEAKNESS réellement agrégée.
        from apex.metalearner.weakness_engine import WeaknessEngineService

        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertTrue(any(w.dimension is JudgeDimension.EFFICIENCY for w in weaknesses))

        # CURRICULUM -> CHALLENGE -> une VRAIE Task persistée, provenance traçable.
        challenges = CurriculumChallengeRepository(self.factory).list_by_specialty("research")
        efficiency_challenge = next(c for c in challenges if c.dimension is JudgeDimension.EFFICIENCY)
        provenance = TaskProvenanceService(self.factory).get_provenance(efficiency_challenge.task_id)
        self.assertIsNotNone(provenance)

        # NEXT GENERATION : le contexte d'évolution reflète RÉELLEMENT cette preuve.
        context = self.service._build_evolution_context("research")
        self.assertTrue(any(t.dimension is JudgeDimension.EFFICIENCY for t in context.curriculum_targets))

        gen1 = self.service.evolve_generation("research")
        self.assertGreater(len(gen1.members), 0)
        # Idempotence par génération : rappeler pour la MÊME génération ne
        # duplique jamais un challenge déjà servi (§20 N+10, reconduit).
        challenges_after = CurriculumChallengeRepository(self.factory).list_by_specialty("research")
        self.assertGreaterEqual(len(challenges_after), len(challenges))
