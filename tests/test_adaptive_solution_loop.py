"""Tests d'`apex.metalearner.adaptive_solution_loop` — la boucle
d'apprentissage adaptative de la mission N+20 : G0 -> G1 -> ... où la
stratégie de chaque génération est RÉELLEMENT dérivée de l'expérience
persistée de la génération précédente.

Même discipline que `tests/test_evalrunner_generated_solution.py` : sandbox
RÉELLE (vraie `AgentRuntime` + `LinuxNamespaceSandbox` namespaced),
`ModelProvider` un double fidèle au contrat (aucun Ollama réel joignable
dans ce bac à sable, voir le rapport N+20 pour le détail). Le test le plus
important de ce fichier (`TestG0ToG1RealDependency`) prouve exactement la
propriété exigée par la mission : si l'expérience de G0 est supprimée, la
décision de G1 change RÉELLEMENT (retombe sur sa stratégie de repli)."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine

from apex.agents.runtime import AgentRuntime
from apex.config.schema import Capability, ComputePool, ModelEndpoint, PoolLocation, TrustLevel
from apex.config.loader import load_config
from apex.db.engine import build_session_factory, session_scope
from apex.db.models import Base, ExperienceRow
from apex.evalrunner.model import EvalMethod
from apex.evalrunner.service import EvalRunnerService
from apex.metalearner.adaptive_solution_loop import (
    AdaptiveLoopConfig,
    decide_next_strategies,
    run_adaptive_learning_loop,
)
from apex.metalearner.experience import Experience, ExperienceRepository, build_experience
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.metalearner.generated_solution_cycle import CandidateStrategy
from apex.metalearner.population_fitness import FitnessResult
from apex.models.model import GenerationRequest, GenerationResult, ModelCapabilityReport
from apex.models.provider import ModelProvider
from apex.obs.capability import CapabilityStatus
from apex.obs.status import Measurement
from apex.oracles.model import OracleVerdict
from apex.policy.model import Subject
from apex.sandbox.model import ExecutionStatus, ResourceLimits
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.taskgen.solution_tasks import build_palindrome_task_spec

_CORRECT_IS_PALINDROME = (
    "def is_palindrome(s: str) -> bool:\n"
    "    cleaned = s.lower()\n"
    "    return cleaned == cleaned[::-1]\n"
)
_WRONG_IS_PALINDROME = "def is_palindrome(s: str) -> bool:\n    return False\n"


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


def an_endpoint(**overrides) -> ModelEndpoint:
    defaults = {
        "pool_id": "local", "runtime": "ollama", "url": "http://127.0.0.1:11434",
        "served_model": "qwen2.5-coder:7b-instruct", "max_context": 8192,
    }
    defaults.update(overrides)
    return ModelEndpoint(**defaults)


class _FakeModelProvider(ModelProvider):
    """Double fidèle au contrat `ModelProvider` (jamais un vrai modèle) —
    `response_by_temperature` simule des stratégies de génération réellement
    distinctes, exactement comme dans `tests/test_evalrunner_generated_solution.py`."""

    def __init__(self, *, response_by_temperature: dict[float | None, str], model_used: str = "fake-model") -> None:
        self.response_by_temperature = response_by_temperature
        self.model_used = model_used
        self.received_requests: list[GenerationRequest] = []

    def detect(self) -> ModelCapabilityReport:
        return ModelCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="fake-test-double",
            detail="double de test — jamais un vrai backend",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.received_requests.append(request)
        # Le mapping est cherché par valeur exacte de température — un test
        # dont l'assertion échoue signale que la décision adaptative n'a
        # PAS demandé la température attendue (voir chaque test pour le
        # raisonnement précis).
        text = self.response_by_temperature.get(round(request.temperature, 6) if request.temperature is not None else None)
        if text is None:
            # Repli explicite : toujours une réponse VALIDE mais fausse,
            # jamais une exception qui masquerait une température non prévue.
            text = _WRONG_IS_PALINDROME
        return GenerationResult(
            text=text, model_used=self.model_used, tokens_prompt=Measurement.real(20),
            tokens_completion=Measurement.real(15), finish_reason="stop", elapsed_s=0.01, facts={"fake": True},
        )


class _AdaptiveLoopFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.runtime = AgentRuntime(self.factory)
        self.task_service = TaskBankService(self.factory)
        self.evalrunner = EvalRunnerService(self.factory, agent_runtime=self.runtime)
        self.experience_repo = ExperienceRepository(self.factory)
        self.endpoint = an_endpoint()
        spec = build_palindrome_task_spec()
        self.task = self.task_service.create(
            split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
            command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness, expected_answer=spec.expected_answer,
        )


class TestG0ToG1RealDependency(_AdaptiveLoopFixture):
    """LE test central de N+20 (§ mission) : G1 doit être RÉELLEMENT
    influencée par ce qu'APEX a appris de G0."""

    def test_a_g0_wrong_answer_leads_g1_to_a_higher_temperature_that_succeeds(self) -> None:
        # G0 : une seule stratégie à température 0.7 -> réponse FAUSSE.
        # `_mutate_from_experience` pour INCORRECT_SOLUTION calcule
        # exactement 0.7 + 0.4 = 1.1 pour G1 (voir la docstring du module).
        provider = _FakeModelProvider(response_by_temperature={0.7: _WRONG_IS_PALINDROME, 1.1: _CORRECT_IS_PALINDROME})
        config = AdaptiveLoopConfig(max_generations=2, target_fitness=None, no_improvement_patience=None)
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]

        result = run_adaptive_learning_loop(
            self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
            endpoint=self.endpoint, pool=a_pool(), initial_strategies=initial, config=config,
            provider=provider,
        )

        self.assertEqual(len(result.generations), 2)
        gen0, gen1 = result.generations
        self.assertIs(gen0.best_experience.failure_category, FailureCategory.INCORRECT_SOLUTION)
        self.assertIs(gen1.best_experience.oracle_verdict, OracleVerdict.PASS)
        self.assertIs(gen1.best_experience.failure_category, FailureCategory.SUCCESS)
        # La preuve numérique exigée par la mission : G1 fitness > G0 fitness.
        self.assertGreater(gen1.best_experience.fitness, gen0.best_experience.fitness)
        self.assertEqual(result.stop_reason, "max_generations_reached")
        self.assertEqual(result.best_overall_generation, 1)
        # Traçabilité (§ mission) : task_id/candidate_id/model_id/generation_number/
        # experience_id/fitness/oracle_verdict/failure_category — tous réels.
        self.assertEqual(gen1.best_experience.task_id, self.task.id)
        self.assertEqual(gen1.best_experience.model_id, provider.model_used)
        self.assertEqual(gen1.best_experience.generation_number, 1)
        self.assertIsNotNone(gen1.best_experience.id)
        # Généalogie réelle : G1 pointe vers le meilleur de G0.
        self.assertEqual(gen1.best_experience.parent_experience_id, gen0.best_experience.id)
        self.assertEqual(gen1.best_experience.parent_generation, 0)
        self.assertIn("incorrecte", gen1.mutation_description)
        self.assertAlmostEqual(
            gen1.best_experience.improvement_delta, gen1.best_experience.fitness - gen0.best_experience.fitness,
        )

    def test_b_deleting_g0_experience_changes_g1_decision(self) -> None:
        """La propriété exacte exigée par la mission : si l'expérience de
        G0 disparaît, la décision de G1 n'est PLUS la même — elle retombe
        sur la stratégie de repli (`base_strategies` inchangées), jamais la
        même mutation informée."""
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]

        clone = self.runtime.spawn(Subject.RESEARCH, pool=a_pool(), genome_ref="sha256:x", spawned_by=Subject.CORE)
        run = self.evalrunner.run_generated_solution(
            task_id=self.task.id, clone_id=clone.clone_id, endpoint=self.endpoint,
            method=EvalMethod.EXPERIMENTATION,
            provider=_FakeModelProvider(response_by_temperature={0.7: _WRONG_IS_PALINDROME}),
            genome_content=initial[0].genome_content,
        )
        from apex.metalearner.population_fitness import compute_fitness

        limits = ResourceLimits.from_config(load_config().sandbox)
        fitness = compute_fitness(run, limits=limits)
        experience = build_experience(
            run, fitness, strategy_id="g0-mut0", generation_number=0,
            generation_config=initial[0].genome_content, created_by=Subject.CORE.value,
        )
        self.experience_repo.create(experience)

        with_experience, description_with = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=initial,
        )
        self.assertNotEqual(with_experience[0].genome_content, initial[0].genome_content)
        self.assertNotIn("aucune expérience", description_with)

        # Supprime réellement la ligne persistée (accès direct DB, comme un
        # test le ferait pour simuler une perte d'historique).
        with session_scope(self.factory) as session:
            row = session.get(ExperienceRow, experience.id)
            session.delete(row)

        without_experience, description_without = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=initial,
        )
        self.assertEqual(without_experience, tuple(initial))
        self.assertIn("aucune expérience", description_without)
        self.assertNotEqual(with_experience, without_experience)


class TestDecideNextStrategies(_AdaptiveLoopFixture):
    def _persist(self, *, failure_category_text: str, temperature: float, fitness: float) -> Experience:
        from apex.oracles.model import OracleVerdict as OV

        verdict = OV.PASS if failure_category_text == "success" else OV.FAIL
        experience = Experience(
            id=f"exp-{failure_category_text}-{temperature}", task_id=self.task.id, eval_run_id="run-x",
            candidate_id="run-x", strategy_id="g0-mut0", generation_number=0,
            generation_config={"generation": {"temperature": temperature}}, oracle_verdict=verdict,
            fitness=fitness, failure_category=FailureCategory(failure_category_text), created_by="core",
        )
        self.experience_repo.create(experience)
        return experience

    def test_a_no_prior_experience_returns_base_strategies_unchanged(self) -> None:
        base = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]
        strategies, description = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=base,
        )
        self.assertEqual(strategies, tuple(base))
        self.assertIn("aucune expérience", description)

    def test_b_success_reduces_temperature(self) -> None:
        self._persist(failure_category_text="success", temperature=0.8, fitness=1.2)
        base = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.8}})]
        strategies, description = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=base,
        )
        self.assertLess(strategies[0].genome_content["generation"]["temperature"], 0.8)
        self.assertIn("SUCCÈS", description)

    def test_c_incorrect_solution_increases_temperature_and_adds_hint(self) -> None:
        self._persist(failure_category_text="incorrect_solution", temperature=0.5, fitness=-0.1)
        base = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.5}})]
        strategies, description = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=base,
        )
        self.assertGreater(strategies[0].genome_content["generation"]["temperature"], 0.5)
        self.assertIn("prompt_suffix", strategies[0].genome_content["generation"])
        self.assertIn("cas limites", strategies[0].genome_content["generation"]["prompt_suffix"])

    def test_d_invalid_candidate_strongly_reduces_temperature_and_adds_format_hint(self) -> None:
        self._persist(failure_category_text="invalid_candidate", temperature=1.5, fitness=-0.3)
        base = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 1.5}})]
        strategies, description = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=base,
        )
        self.assertLess(strategies[0].genome_content["generation"]["temperature"], 1.5 * 0.5)
        self.assertIn("valide et exécutable", strategies[0].genome_content["generation"]["prompt_suffix"])

    def test_e_infrastructure_failure_leaves_temperature_unchanged(self) -> None:
        self._persist(failure_category_text="infrastructure_failure", temperature=0.6, fitness=-0.3)
        base = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.6}})]
        strategies, description = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=base,
        )
        self.assertEqual(strategies[0].genome_content["generation"]["temperature"], 0.6)
        self.assertIn("infrastructure", description)

    def test_f_multiple_candidates_are_spread_not_identical(self) -> None:
        self._persist(failure_category_text="incorrect_solution", temperature=0.5, fitness=-0.1)
        base = [
            CandidateStrategy(label="a", genome_content={"generation": {"temperature": 0.5}}),
            CandidateStrategy(label="b", genome_content={"generation": {"temperature": 0.5}}),
            CandidateStrategy(label="c", genome_content={"generation": {"temperature": 0.5}}),
        ]
        strategies, _ = decide_next_strategies(
            self.experience_repo, task_id=self.task.id, prior_generation_number=0, base_strategies=base,
        )
        temperatures = {s.genome_content["generation"]["temperature"] for s in strategies}
        self.assertEqual(len(temperatures), 3)


class TestStopCriteria(_AdaptiveLoopFixture):
    def test_a_target_fitness_stops_after_first_success(self) -> None:
        provider = _FakeModelProvider(response_by_temperature={0.7: _CORRECT_IS_PALINDROME})
        config = AdaptiveLoopConfig(max_generations=5, target_fitness=1.0, no_improvement_patience=None)
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]
        result = run_adaptive_learning_loop(
            self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
            endpoint=self.endpoint, pool=a_pool(), initial_strategies=initial, config=config, provider=provider,
        )
        self.assertEqual(len(result.generations), 1)
        self.assertEqual(result.stop_reason, "target_fitness_reached")

    def test_b_no_improvement_patience_stops_early_when_nothing_ever_improves(self) -> None:
        # Toute température retombe sur la même réponse fausse (repli du
        # double) — le fitness ne s'améliore jamais, la patience doit
        # arrêter la boucle bien avant `max_generations`.
        provider = _FakeModelProvider(response_by_temperature={})
        config = AdaptiveLoopConfig(max_generations=5, target_fitness=None, no_improvement_patience=1)
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]
        result = run_adaptive_learning_loop(
            self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
            endpoint=self.endpoint, pool=a_pool(), initial_strategies=initial, config=config, provider=provider,
        )
        self.assertLess(len(result.generations), 5)
        self.assertEqual(result.stop_reason, "no_improvement_patience_exhausted")

    def test_c_max_generations_is_hit_when_no_other_criterion_fires(self) -> None:
        provider = _FakeModelProvider(response_by_temperature={})
        config = AdaptiveLoopConfig(max_generations=3, target_fitness=None, no_improvement_patience=None)
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]
        result = run_adaptive_learning_loop(
            self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
            endpoint=self.endpoint, pool=a_pool(), initial_strategies=initial, config=config, provider=provider,
        )
        self.assertEqual(len(result.generations), 3)
        self.assertEqual(result.stop_reason, "max_generations_reached")

    def test_d_max_generations_must_be_at_least_one(self) -> None:
        with self.assertRaises(ValueError):
            AdaptiveLoopConfig(max_generations=0)

    def test_e_empty_initial_strategies_is_refused(self) -> None:
        config = AdaptiveLoopConfig(max_generations=2)
        with self.assertRaises(ValueError):
            run_adaptive_learning_loop(
                self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
                endpoint=self.endpoint, pool=a_pool(), initial_strategies=[], config=config,
                provider=_FakeModelProvider(response_by_temperature={}),
            )


class TestRunIsolation(_AdaptiveLoopFixture):
    """Défaut confirmé par le Continuous Improvement Engine (cycle 1) :
    sans `experiment_ref`, la G1 d'une exécution dérivait de la meilleure
    G0 de TOUTES les exécutions de la tâche, alors que sa trace affirmait
    dériver de sa propre G0."""

    def _run(self, provider, *, max_generations, experiment_ref=None):
        config = AdaptiveLoopConfig(max_generations=max_generations, target_fitness=None, no_improvement_patience=None)
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]
        return run_adaptive_learning_loop(
            self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
            endpoint=self.endpoint, pool=a_pool(), initial_strategies=initial, config=config,
            provider=provider, experiment_ref=experiment_ref,
        )

    def test_a_second_run_decides_from_its_own_g0_not_from_an_earlier_success(self) -> None:
        first = self._run(_FakeModelProvider(response_by_temperature={0.7: _CORRECT_IS_PALINDROME}), max_generations=1)
        provider_b = _FakeModelProvider(
            response_by_temperature={0.7: _WRONG_IS_PALINDROME, 1.1: _CORRECT_IS_PALINDROME, 0.35: _WRONG_IS_PALINDROME}
        )
        second = self._run(provider_b, max_generations=2)

        self.assertEqual(round(provider_b.received_requests[1].temperature, 6), 1.1)
        gen0, gen1 = second.generations
        self.assertIs(gen0.best_experience.failure_category, FailureCategory.INCORRECT_SOLUTION)
        self.assertIn("incorrecte", gen1.mutation_description)
        self.assertIs(gen1.best_experience.failure_category, FailureCategory.SUCCESS)
        self.assertEqual(gen1.best_experience.parent_experience_id, gen0.best_experience.id)
        self.assertIsNotNone(first.experiment_ref)
        self.assertNotEqual(first.experiment_ref, second.experiment_ref)

    def test_b_every_experience_of_a_run_carries_its_experiment_ref(self) -> None:
        result = self._run(_FakeModelProvider(response_by_temperature={}), max_generations=2)
        refs = {e.experiment_ref for g in result.generations for e in g.experiences}
        self.assertEqual(refs, {result.experiment_ref})

    def test_c_an_explicit_experiment_ref_is_kept(self) -> None:
        result = self._run(_FakeModelProvider(response_by_temperature={}), max_generations=1, experiment_ref="exp-42")
        self.assertEqual(result.experiment_ref, "exp-42")


class TestSandboxBoundary(_AdaptiveLoopFixture):
    def test_a_a_candidate_that_tries_to_crash_the_host_process_stays_sandboxed(self) -> None:
        """Un candidat hostile (tentative d'affecter le processus hôte)
        n'échappe jamais au sous-processus sandboxé — même garantie que
        `tests/test_evalrunner_generated_solution.py::test_d`, vérifiée ici
        au niveau de la boucle adaptative complète (le test complète
        normalement, jamais un crash de CE processus)."""
        hostile_code = "raise SystemExit(1)\n"
        provider = _FakeModelProvider(response_by_temperature={0.7: hostile_code})
        config = AdaptiveLoopConfig(max_generations=1)
        initial = [CandidateStrategy(label="g0-mut0", genome_content={"generation": {"temperature": 0.7}})]
        result = run_adaptive_learning_loop(
            self.evalrunner, self.runtime, self.experience_repo, task_id=self.task.id,
            endpoint=self.endpoint, pool=a_pool(), initial_strategies=initial, config=config, provider=provider,
        )
        # Ce process Python (le test lui-même) tourne toujours normalement —
        # la preuve que `raise SystemExit(1)` a été confiné au sous-processus.
        self.assertEqual(len(result.generations), 1)
        self.assertIn(
            result.generations[0].best_experience.failure_category,
            (FailureCategory.INVALID_CANDIDATE, FailureCategory.EXECUTION_FAILURE),
        )


if __name__ == "__main__":
    unittest.main()
