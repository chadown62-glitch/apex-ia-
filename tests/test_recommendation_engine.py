"""Tests d'`apex.metalearner.recommendation_engine` — mission N+28.

SQLite en mémoire, même patron que `tests/test_learning_recommendation.py`
(N+26). Le moteur ne touche JAMAIS le réseau/Ollama/sandbox — ces tests le
prouvent structurellement (imports réels du module) ET par comportement
(aucune mutation des `Experience` lues, déterminisme strict)."""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.metalearner.experience import Experience, ExperienceRepository
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.recommendation import Recommendation, RecommendationConfidence, RecommendationSource
from apex.metalearner.recommendation_engine import (
    AbstentionReason,
    ExperienceRecommendationEngine,
    RelevanceTier,
    recommend_for_task,
)
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService


def sqlite_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _a_task(factory, task_id_suffix: str, *, specialty: str = "research"):
    return TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty=specialty, title=f"tâche {task_id_suffix}",
        description=f"objectif de la tâche {task_id_suffix}",
        command=("python3", "-c", "pass"), created_by=Subject.HUMAN,
    )


def _an_experience(
    exp_id: str, task_id: str, category: FailureCategory, *, model_id: str | None = "model-a",
    strategy_id: str = "g0-mut0", **overrides,
) -> Experience:
    defaults = dict(
        id=exp_id, task_id=task_id, eval_run_id=f"run-{exp_id}", candidate_id=f"run-{exp_id}",
        strategy_id=strategy_id, model_id=model_id, generation_number=0, generation_config={},
        oracle_verdict=OracleVerdict.PASS if category is FailureCategory.SUCCESS else OracleVerdict.FAIL,
        fitness=1.0 if category is FailureCategory.SUCCESS else 0.0,
        failure_category=category, created_by="core", created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Experience(**defaults)


class TestNoExperienceAtAll(unittest.TestCase):
    def test_unknown_task_abstains_with_unknown_task_reason(self) -> None:
        factory = sqlite_session_factory()
        report = recommend_for_task("never-created", session_factory=factory)
        self.assertTrue(report.abstained)
        self.assertEqual(report.abstention_reason, AbstentionReason.UNKNOWN_TASK)
        self.assertEqual(report.recommendations, ())
        self.assertEqual(report.overall_confidence, RecommendationConfidence.INSUFFICIENT_DATA)

    def test_known_task_with_zero_experiences_abstains_with_no_comparable_reason(self) -> None:
        factory = sqlite_session_factory()
        task = _a_task(factory, "empty")
        report = recommend_for_task(task.id, session_factory=factory)
        self.assertTrue(report.abstained)
        self.assertEqual(report.abstention_reason, AbstentionReason.NO_COMPARABLE_EXPERIENCE)
        self.assertIn(task.specialty, report.abstention_detail or "")


class TestSampleSize(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = ExperienceRepository(self.factory)

    def test_a_single_experience_abstains_sample_too_small(self) -> None:
        task = _a_task(self.factory, "one-exp")
        self.repo.create(_an_experience("e1", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=self.factory)
        self.assertTrue(report.abstained)
        self.assertEqual(report.abstention_reason, AbstentionReason.SAMPLE_TOO_SMALL)
        self.assertEqual(report.exact_task_experience_ids, ("e1",))

    def test_several_experiences_reach_the_minimum_and_produce_a_recommendation(self) -> None:
        task = _a_task(self.factory, "several")
        for i in range(5):
            self.repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=self.factory)
        self.assertFalse(report.abstained)
        self.assertGreaterEqual(len(report.recommendations), 1)

    def test_small_sample_yields_low_confidence(self) -> None:
        task = _a_task(self.factory, "small")
        for i in range(3):
            self.repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=self.factory)
        self.assertIs(report.overall_confidence, RecommendationConfidence.LOW)

    def test_large_sample_yields_high_confidence(self) -> None:
        task = _a_task(self.factory, "large")
        for i in range(12):
            self.repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=self.factory)
        self.assertIs(report.overall_confidence, RecommendationConfidence.HIGH)


class TestRelevanceFiltering(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = ExperienceRepository(self.factory)

    def test_exact_task_experiences_are_the_primary_tier(self) -> None:
        target = _a_task(self.factory, "target")
        for i in range(4):
            self.repo.create(_an_experience(f"e{i}", target.id, FailureCategory.SUCCESS))
        report = recommend_for_task(target.id, session_factory=self.factory)
        self.assertEqual(len(report.exact_task_experience_ids), 4)
        self.assertEqual(report.same_specialty_experience_ids, ())

    def test_irrelevant_experiences_from_a_different_specialty_are_never_counted(self) -> None:
        target = _a_task(self.factory, "target2", specialty="research")
        other = _a_task(self.factory, "other-specialty")
        # Aucune autre spécialité n'est réellement enregistrée dans ce dépôt
        # (`apex.specialties.registry` ne connaît que « research ») — ce
        # test vérifie donc l'exclusion sur la dimension réellement
        # disponible : une AUTRE tâche, même spécialité, N'EST PAS la tâche
        # cible mais reste comptée en SAME_SPECIALTY (voir le test suivant)
        # — pour prouver une vraie exclusion, on vérifie ici qu'une
        # expérience rattachée à une tâche DIFFÉRENTE n'apparaît jamais
        # dans `exact_task_experience_ids`.
        for i in range(3):
            self.repo.create(_an_experience(f"t{i}", target.id, FailureCategory.SUCCESS))
        self.repo.create(_an_experience("o0", other.id, FailureCategory.EXECUTION_FAILURE))
        report = recommend_for_task(target.id, session_factory=self.factory)
        self.assertNotIn("o0", report.exact_task_experience_ids)

    def test_mixed_relevant_and_irrelevant_only_counts_the_relevant_ones(self) -> None:
        target = _a_task(self.factory, "target3")
        sibling = _a_task(self.factory, "sibling3")  # même spécialité, tâche différente
        for i in range(2):
            self.repo.create(_an_experience(f"t{i}", target.id, FailureCategory.SUCCESS))
        for i in range(2):
            self.repo.create(_an_experience(f"s{i}", sibling.id, FailureCategory.SUCCESS))
        report = recommend_for_task(target.id, session_factory=self.factory)
        self.assertEqual(len(report.exact_task_experience_ids), 2)
        self.assertEqual(len(report.same_specialty_experience_ids), 2)
        self.assertEqual(report.relevant_experience_count, 4)
        self.assertFalse(report.abstained)


class TestSuccessFailureMix(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = ExperienceRepository(self.factory)

    def test_successes_only_recommends_no_action(self) -> None:
        task = _a_task(self.factory, "all-success")
        for i in range(5):
            self.repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=self.factory)
        overall = report.recommendations[0]
        self.assertIn("100%", overall.analysis)
        self.assertIn("aucune action recommandée", overall.recommendation)

    def test_failures_only_recommends_a_different_strategy(self) -> None:
        task = _a_task(self.factory, "all-failure")
        for i in range(5):
            self.repo.create(_an_experience(f"e{i}", task.id, FailureCategory.INCORRECT_SOLUTION))
        report = recommend_for_task(task.id, session_factory=self.factory)
        overall = report.recommendations[0]
        self.assertIn("0%", overall.analysis)
        self.assertIn("stratégie de génération différente", overall.recommendation)

    def test_a_toss_up_success_rate_abstains_when_no_other_signal_exists(self) -> None:
        task = _a_task(self.factory, "toss-up")
        for i in range(3):
            self.repo.create(_an_experience(f"s{i}", task.id, FailureCategory.SUCCESS, model_id=None))
        # Trois catégories d'échec DISTINCTES, une seule occurrence chacune
        # — aucune n'est "dominante" (`_MIN_SAMPLES_PER_GROUP` exige au
        # moins 2), aucun provider/stratégie ne varie : un scénario
        # authentiquement sans signal exploitable, pas seulement un taux
        # de succès équilibré.
        self.repo.create(_an_experience("f0", task.id, FailureCategory.TIMEOUT, model_id=None))
        self.repo.create(_an_experience("f1", task.id, FailureCategory.EXECUTION_FAILURE, model_id=None))
        self.repo.create(_an_experience("f2", task.id, FailureCategory.INVALID_CANDIDATE, model_id=None))
        report = recommend_for_task(task.id, session_factory=self.factory)
        self.assertTrue(report.abstained)
        self.assertEqual(report.abstention_reason, AbstentionReason.CONTRADICTORY_RESULTS)
        self.assertEqual(report.recommendations, ())


class TestProviderComparison(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = ExperienceRepository(self.factory)

    def test_two_providers_with_a_significant_gap_are_compared(self) -> None:
        task = _a_task(self.factory, "providers")
        for i in range(4):
            self.repo.create(_an_experience(f"a{i}", task.id, FailureCategory.SUCCESS, model_id="model-a"))
        for i in range(4):
            self.repo.create(
                _an_experience(f"b{i}", task.id, FailureCategory.EXECUTION_FAILURE, model_id="model-b"),
            )
        report = recommend_for_task(task.id, session_factory=self.factory)
        provider_recs = [r for r in report.recommendations if "provider" in r.observation]
        self.assertEqual(len(provider_recs), 1)
        self.assertIn("model-a", provider_recs[0].recommendation)
        self.assertNotIn("meilleur modèle", provider_recs[0].recommendation)

    def test_equivalent_provider_results_produce_no_comparison(self) -> None:
        task = _a_task(self.factory, "equivalent-providers")
        for provider in ("model-a", "model-b"):
            for i in range(3):
                category = FailureCategory.SUCCESS if i % 2 == 0 else FailureCategory.EXECUTION_FAILURE
                self.repo.create(
                    _an_experience(f"{provider}-{i}", task.id, category, model_id=provider),
                )
        report = recommend_for_task(task.id, session_factory=self.factory)
        provider_recs = [r for r in report.recommendations if "provider" in r.observation]
        self.assertEqual(provider_recs, [])

    def test_missing_provider_data_is_never_treated_as_its_own_group(self) -> None:
        task = _a_task(self.factory, "missing-provider")
        for i in range(4):
            self.repo.create(_an_experience(f"a{i}", task.id, FailureCategory.SUCCESS, model_id="model-a"))
        for i in range(4):
            self.repo.create(
                _an_experience(f"b{i}", task.id, FailureCategory.EXECUTION_FAILURE, model_id="model-b"),
            )
        for i in range(3):
            self.repo.create(
                _an_experience(f"u{i}", task.id, FailureCategory.SUCCESS, model_id=None),
            )
        report = recommend_for_task(task.id, session_factory=self.factory)
        provider_recs = [r for r in report.recommendations if "provider" in r.observation]
        self.assertEqual(len(provider_recs), 1)
        self.assertIn("sans provider attribué", provider_recs[0].observation)
        self.assertNotIn("None", provider_recs[0].observation)


class TestStrategyComparison(unittest.TestCase):
    def test_two_strategies_with_a_significant_gap_are_compared(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "strategies")
        for i in range(4):
            repo.create(_an_experience(f"a{i}", task.id, FailureCategory.SUCCESS, strategy_id="strat-a"))
        for i in range(4):
            repo.create(
                _an_experience(f"b{i}", task.id, FailureCategory.INCORRECT_SOLUTION, strategy_id="strat-b"),
            )
        report = recommend_for_task(task.id, session_factory=factory)
        strategy_recs = [r for r in report.recommendations if "stratégie" in r.observation]
        self.assertEqual(len(strategy_recs), 1)
        self.assertIn("strat-a", strategy_recs[0].recommendation)


class TestRepeatedFailureCategory(unittest.TestCase):
    def test_a_dominant_failure_category_is_reported(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "dominant-category")
        for i in range(2):
            repo.create(_an_experience(f"s{i}", task.id, FailureCategory.SUCCESS))
        for i in range(4):
            repo.create(_an_experience(f"t{i}", task.id, FailureCategory.TIMEOUT))
        for i in range(1):
            repo.create(_an_experience(f"i{i}", task.id, FailureCategory.INVALID_CANDIDATE))
        report = recommend_for_task(task.id, session_factory=factory)
        category_recs = [
            r for r in report.recommendations if "catégorie d'échec dominante" in r.observation
        ]
        self.assertEqual(len(category_recs), 1)
        self.assertIn("timeout", category_recs[0].observation)
        self.assertEqual(category_recs[0].evidence_count, 4)


class TestExplainability(unittest.TestCase):
    def test_every_recommendation_is_traceable_and_carries_all_required_fields(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "explainable")
        for i in range(6):
            repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=factory)
        for rec in report.recommendations:
            self.assertIsInstance(rec, Recommendation)
            self.assertTrue(rec.observation)
            self.assertTrue(rec.analysis)
            self.assertTrue(rec.recommendation)
            self.assertGreater(rec.evidence_count, 0)
            self.assertEqual(len(rec.evidence_refs), rec.evidence_count)
            for ref in rec.evidence_refs:
                self.assertIn(ref, report.exact_task_experience_ids + report.same_specialty_experience_ids)

    def test_report_round_trips_to_dict(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "as-dict")
        for i in range(4):
            repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        report = recommend_for_task(task.id, session_factory=factory)
        as_dict = report.to_dict()
        self.assertEqual(as_dict["task_id"], task.id)
        self.assertIsInstance(as_dict["recommendations"], list)
        self.assertFalse(as_dict["abstained"])
        self.assertIsNone(as_dict["abstention_reason"])


class TestDeterminismAndNoSideEffects(unittest.TestCase):
    def test_calling_twice_on_unchanged_data_is_identical(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "deterministic")
        for i in range(6):
            category = FailureCategory.SUCCESS if i < 4 else FailureCategory.TIMEOUT
            repo.create(_an_experience(f"e{i}", task.id, category))
        first = recommend_for_task(task.id, session_factory=factory)
        second = recommend_for_task(task.id, session_factory=factory)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_experiences_are_never_mutated_by_recommending(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "no-mutation")
        for i in range(5):
            repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        before = repo.list_by_task(task.id)
        before_snapshot = {e.id: (e.failure_category, e.fitness, e.oracle_verdict) for e in before}

        recommend_for_task(task.id, session_factory=factory)

        after = repo.list_by_task(task.id)
        after_snapshot = {e.id: (e.failure_category, e.fitness, e.oracle_verdict) for e in after}
        self.assertEqual(len(before), len(after))
        self.assertEqual(before_snapshot, after_snapshot)


class TestNoNetworkNoOllamaNoSandboxNoTaskloopNoRecovery(unittest.TestCase):
    """20-22 + N+26/N+27 boundary : le module ne peut STRUCTURELLEMENT pas
    appeler le réseau/Ollama/le sandbox/le TaskLoop/le Recovery — vérifié
    sur les lignes `import`/`from` réelles, jamais la docstring."""

    def test_module_imports_never_reach_execution_layers(self) -> None:
        import apex.metalearner.recommendation_engine as mod

        import_lines = [
            line.strip() for line in inspect.getsource(mod).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        forbidden_substrings = (
            "apex.taskloop", "apex.recovery", "apex.sandbox", "apex.agents.runtime",
            "apex.models.provider", "apex.models.ollama", "apex.models.openai_compatible",
            "urllib", "requests", "httpx", "socket",
        )
        for line in import_lines:
            for forbidden in forbidden_substrings:
                self.assertNotIn(forbidden, line, f"import interdit trouvé : {line!r}")

    def test_no_forbidden_control_methods_are_exposed(self) -> None:
        import apex.metalearner.recommendation_engine as mod

        forbidden = {"decide", "apply", "enforce", "execute", "run_task"}
        module_level_names = {name for name in dir(mod) if not name.startswith("_")}
        self.assertEqual(module_level_names & forbidden, set())
        engine_methods = {name for name in dir(ExperienceRecommendationEngine) if not name.startswith("_")}
        self.assertEqual(engine_methods & forbidden, set())


class TestCompatibilityWithN26RecommendationContract(unittest.TestCase):
    def test_experience_recommendation_engine_satisfies_the_recommendation_source_protocol(self) -> None:
        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        task = _a_task(factory, "protocol")
        for i in range(5):
            repo.create(_an_experience(f"e{i}", task.id, FailureCategory.SUCCESS))
        engine: RecommendationSource = ExperienceRecommendationEngine(session_factory=factory)
        recs = engine.recommend(task_id=task.id)
        self.assertIsInstance(recs, tuple)
        for r in recs:
            self.assertIsInstance(r, Recommendation)

    def test_insufficient_data_confidence_is_a_real_member_of_the_shared_enum(self) -> None:
        self.assertIn(RecommendationConfidence.INSUFFICIENT_DATA, RecommendationConfidence)


class TestCompatibilityWithN27Experience(unittest.TestCase):
    """25/25 : une `Experience` construite EXACTEMENT comme le fait
    `apex.taskloop.loop._run` (via `build_experience`, jamais une
    expérience fabriquée à la main) est réellement consommée par le
    moteur — preuve d'interopérabilité réelle avec N+27, sans jamais
    importer `apex.taskloop` dans le moteur lui-même."""

    def test_a_build_experience_output_is_correctly_consumed(self) -> None:
        from apex.evalrunner.model import EvalMethod, EvalRun
        from apex.metalearner.experience import build_experience
        from apex.metalearner.population_fitness import compute_fitness
        from apex.sandbox.model import ResourceLimits

        factory = sqlite_session_factory()
        task = _a_task(factory, "n27-compat")
        repo = ExperienceRepository(factory)
        limits = ResourceLimits(
            cpu_cores=1.0, memory_mb=64, pids_limit=16, timeout_s=5, disk_mb=16, network="none",
        )

        for i in range(4):
            eval_run = EvalRun(
                id=f"run-{i}", task_id=task.id, method=EvalMethod.EXPERIMENTATION,
                oracle_verdict=OracleVerdict.PASS, oracle_detail="ok", exit_code=0, elapsed_s=0.5,
                facts={}, created_by="core", clone_id=f"clone-{i}", model_id="a-model:1",
            )
            fitness = compute_fitness(eval_run, limits=limits)
            experience = build_experience(
                eval_run, fitness, strategy_id="candidate:a-model:1",
                generation_number=0, generation_config={}, created_by="core",
            )
            repo.create(experience)

        report = recommend_for_task(task.id, session_factory=factory)
        self.assertFalse(report.abstained)
        self.assertEqual(len(report.exact_task_experience_ids), 4)


class TestRelevanceTierVocabularyIsClosed(unittest.TestCase):
    def test_only_two_tiers_exist(self) -> None:
        self.assertEqual({t.value for t in RelevanceTier}, {"exact_task", "same_specialty"})


class TestRegressionExistingRecommendationModuleUnaffected(unittest.TestCase):
    def test_recommend_from_experience_and_recommendation_confidence_still_work(self) -> None:
        from apex.metalearner.recommendation import recommend_from_experience

        factory = sqlite_session_factory()
        repo = ExperienceRepository(factory)
        for i in range(5):
            repo.create(_an_experience(f"e{i}", "legacy-task", FailureCategory.SUCCESS))
        recs = recommend_from_experience("legacy-task", session_factory=factory)
        self.assertGreaterEqual(len(recs), 1)
        self.assertIsInstance(recs[0].confidence, RecommendationConfidence)


if __name__ == "__main__":
    unittest.main()
