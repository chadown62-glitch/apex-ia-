"""Tests de `apex.metalearner.capability_benchmark_service`/
`capability_model` (passe « Adversarial Curriculum + Capability
Benchmarking », N+10). Lettres F-V du §19 de la mission citées
explicitement."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.agents.runtime import AgentRuntime
from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.genome.service import GenomeStoreService
from apex.metalearner.capability_benchmark_service import CapabilityBenchmarkService
from apex.metalearner.capability_model import (
    CAPABILITY_DIMENSIONS,
    CapabilityBenchmarkRun,
    CapabilityMeasurement,
)
from apex.metalearner.gauntlet import GauntletRunner
from apex.metalearner.judge_model import (
    GauntletStage,
    JudgeDecision,
    JudgeDimension,
    JudgeFinding,
    JudgeResult,
    JudgeSeverity,
)
from apex.policy.model import Subject
from apex.sandbox.model import ResourceLimits


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def promote_a_genome(factory) -> str:
    genome = GenomeStoreService(factory)
    genome.propose(content={"a": 1}, created_by=Subject.HUMAN)
    proposed = genome.list_all()[0]
    genome.promote(proposed.id, requested_by=Subject.HUMAN)
    return proposed.id


def a_limits() -> ResourceLimits:
    return ResourceLimits(cpu_cores=1, memory_mb=512, pids_limit=32, timeout_s=10.0, disk_mb=64, network="none")


def a_judge_result(dimension, decision, *, score=1.0, confidence=1.0, veto=False, judge_id=None) -> JudgeResult:
    findings = ()
    if veto:
        findings = (JudgeFinding(dimension=dimension, severity=JudgeSeverity.CRITICAL, summary="véto (test)"),)
    return JudgeResult(
        judge_id=judge_id or dimension.value, judge_version="1", dimension=dimension, decision=decision,
        score=score, confidence=confidence, rationale="test", findings=findings,
    )


def a_benchmark_run(
    specialty="research", member_id="m1", generation=1, measurements=None, overall_decision=JudgeDecision.PASS,
) -> CapabilityBenchmarkRun:
    measurements = measurements or tuple(
        CapabilityMeasurement(dimension=d, decision=JudgeDecision.PASS, score=1.0, confidence=1.0, evidence_reference=None)
        for d in CAPABILITY_DIMENSIONS
    )
    return CapabilityBenchmarkRun(
        id="run-1", specialty=specialty, member_id=member_id, generation=generation, genome_version_id="genome-1",
        benchmark_version="1", gauntlet_result_id="gauntlet-1", overall_decision=overall_decision,
        overall_score=1.0, overall_confidence=1.0, measurements=measurements,
    )


class TestBenchmarkIndependenceRealEndToEnd(unittest.TestCase):
    """Lettres F/G du §19 — la batterie de tâches du benchmark est FIXE,
    HELDOUT, et DISTINCTE du curriculum d'entraînement (§12 : jamais un
    candidat déclaré meilleur simplement parce qu'il est meilleur sur les
    exercices qui ont servi à l'améliorer)."""

    def test_benchmark_tasks_are_distinct_from_curriculum_challenge_tasks(self) -> None:
        factory = sqlite_session_factory()
        genome_id = promote_a_genome(factory)
        runtime = AgentRuntime(factory)
        limits = a_limits()

        benchmark_service = CapabilityBenchmarkService(factory, agent_runtime=runtime)
        run = benchmark_service.run_benchmark(
            specialty="research", member_id="m1", generation=0, genome_version_id=genome_id,
            genome_content=None, limits=limits, compared_champion_id=None,
        )
        self.assertIsNotNone(run)
        self.assertEqual(len(run.measurements), len(CAPABILITY_DIMENSIONS))

        # Les tâches du benchmark portent un dataset_ref RÉEL, DISTINCT du
        # curriculum challenge namespace (`edge_case:`/`adversarial:`).
        from apex.tasks.service import TaskBankService

        tasks = TaskBankService(factory).list_by_specialty("research")
        benchmark_task_refs = [t.dataset_ref for t in tasks if t.dataset_ref and t.dataset_ref.startswith("capability_benchmark_v1")]
        self.assertGreaterEqual(len(benchmark_task_refs), 4)
        curriculum_task_refs = [t.dataset_ref for t in tasks if t.dataset_ref and (t.dataset_ref.startswith("edge_case:") or t.dataset_ref.startswith("adversarial:"))]
        self.assertEqual(len(set(benchmark_task_refs) & set(curriculum_task_refs)), 0)

    def test_multidimensional_vector_is_never_reduced_to_one_number(self) -> None:
        """Lettre I du §19."""
        factory = sqlite_session_factory()
        genome_id = promote_a_genome(factory)
        runtime = AgentRuntime(factory)
        service = CapabilityBenchmarkService(factory, agent_runtime=runtime)
        run = service.run_benchmark(
            specialty="research", member_id="m1", generation=0, genome_version_id=genome_id,
            genome_content=None, limits=a_limits(), compared_champion_id=None,
        )
        dimensions = {m.dimension for m in run.measurements}
        self.assertEqual(dimensions, set(CAPABILITY_DIMENSIONS))
        # une dimension peut différer d'une autre — jamais moyennées en un seul champ.
        scores = {m.dimension: m.score for m in run.measurements}
        self.assertTrue(all(isinstance(v, (float, type(None))) for v in scores.values()))


class TestCapabilityDeltaVector(unittest.TestCase):
    """Lettre H du §19 — « +15% correctness -40% security -> PAS une
    amélioration acceptable » : une régression critique reste visible,
    jamais masquée par une moyenne."""

    def test_security_regression_is_flagged_even_with_other_improvements(self) -> None:
        factory = sqlite_session_factory()
        service = CapabilityBenchmarkService(factory, agent_runtime=AgentRuntime(factory))
        champion_measurements = tuple(
            CapabilityMeasurement(dimension=d, decision=JudgeDecision.PASS, score=0.80, confidence=1.0, evidence_reference=None)
            for d in CAPABILITY_DIMENSIONS
        )
        champion_run = a_benchmark_run(member_id="champion", measurements=champion_measurements)

        candidate_measurements = []
        for d in CAPABILITY_DIMENSIONS:
            score = 0.95 if d is JudgeDimension.CORRECTNESS else (0.40 if d is JudgeDimension.SECURITY else 0.80)
            candidate_measurements.append(
                CapabilityMeasurement(dimension=d, decision=JudgeDecision.PASS, score=score, confidence=1.0, evidence_reference=None),
            )
        candidate_run = a_benchmark_run(member_id="candidate", measurements=tuple(candidate_measurements))

        delta = service.compute_delta(candidate_run, reference_run=champion_run, reference_kind="champion")
        self.assertGreater(delta.dimension_deltas[JudgeDimension.CORRECTNESS], 0)
        self.assertLess(delta.dimension_deltas[JudgeDimension.SECURITY], 0)
        self.assertTrue(delta.critical_regression)
        self.assertIn(JudgeDimension.SECURITY, delta.regressed_dimensions)

    def test_no_reference_run_has_no_fabricated_deltas(self) -> None:
        """Lettre S du §19 — preuve insuffisante, jamais un delta inventé."""
        factory = sqlite_session_factory()
        service = CapabilityBenchmarkService(factory, agent_runtime=AgentRuntime(factory))
        candidate_run = a_benchmark_run()
        delta = service.compute_delta(candidate_run, reference_run=None, reference_kind="champion")
        self.assertTrue(all(v is None for v in delta.dimension_deltas.values()))
        self.assertFalse(delta.critical_regression)
        self.assertEqual(set(delta.missing_reference_dimensions), set(CAPABILITY_DIMENSIONS))


class TestPromotionRule(unittest.TestCase):
    """Lettres J/K/L/S du §19 — règle stricte, jamais un `PROMOTE` forcé."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = CapabilityBenchmarkService(self.factory, agent_runtime=AgentRuntime(self.factory))

    def test_critical_regression_vs_champion_blocks_promotion_regardless_of_average(self) -> None:
        champion_measurements = tuple(
            CapabilityMeasurement(dimension=d, decision=JudgeDecision.PASS, score=0.80, confidence=1.0, evidence_reference=None)
            for d in CAPABILITY_DIMENSIONS
        )
        champion_run = a_benchmark_run(member_id="champion", measurements=champion_measurements)
        candidate_measurements = [
            CapabilityMeasurement(
                dimension=d, decision=JudgeDecision.PASS,
                score=(0.40 if d is JudgeDimension.SECURITY else 0.95), confidence=1.0, evidence_reference=None,
            )
            for d in CAPABILITY_DIMENSIONS
        ]
        candidate_run = a_benchmark_run(member_id="candidate", measurements=tuple(candidate_measurements))
        delta = self.service.compute_delta(candidate_run, reference_run=champion_run, reference_kind="champion")

        decision = self.service.decide_promotion(candidate_run, delta, min_confidence=0.6)
        self.assertEqual(decision.decision, JudgeDecision.REJECT)
        self.assertEqual(decision.blocking_dimension, JudgeDimension.SECURITY)

    def test_training_improvement_but_heldout_regression_is_rejected(self) -> None:
        """Lettre K du §19 — un véto sur le benchmark lui-même (ex.
        GENERALIZATION échoue sur la batterie HELDOUT du benchmark) bloque
        la promotion MÊME si le candidat s'est amélioré sur l'entraînement."""
        rejected_run = a_benchmark_run(overall_decision=JudgeDecision.REJECT)
        decision = self.service.decide_promotion(rejected_run, None, min_confidence=0.6)
        self.assertEqual(decision.decision, JudgeDecision.REJECT)

    def test_judge_score_improvement_but_benchmark_unchanged_is_not_blocked_but_not_fabricated_either(self) -> None:
        """Lettre L du §19 — le benchmark indépendant NE PROGRESSE PAS :
        `decide_promotion` retourne `PASS` (non régressé, la règle exacte
        du §13 — « au minimum non régressé »), mais `compute_delta` ne
        prétend JAMAIS une amélioration qui n'a pas eu lieu (delta = 0,
        jamais positif)."""
        champion_measurements = tuple(
            CapabilityMeasurement(dimension=d, decision=JudgeDecision.PASS, score=0.80, confidence=1.0, evidence_reference=None)
            for d in CAPABILITY_DIMENSIONS
        )
        champion_run = a_benchmark_run(member_id="champion", measurements=champion_measurements)
        candidate_run = a_benchmark_run(member_id="candidate", measurements=champion_measurements)
        delta = self.service.compute_delta(candidate_run, reference_run=champion_run, reference_kind="champion")
        self.assertTrue(all(v == 0.0 for v in delta.dimension_deltas.values()))
        decision = self.service.decide_promotion(candidate_run, delta, min_confidence=0.6)
        self.assertEqual(decision.decision, JudgeDecision.PASS)

    def test_inconclusive_benchmark_never_promotes(self) -> None:
        """Lettre S du §19."""
        inconclusive_run = a_benchmark_run(overall_decision=JudgeDecision.INCONCLUSIVE)
        decision = self.service.decide_promotion(inconclusive_run, None, min_confidence=0.6)
        self.assertEqual(decision.decision, JudgeDecision.INCONCLUSIVE)

    def test_low_confidence_below_threshold_is_inconclusive_never_a_forced_pass(self) -> None:
        low_confidence_run = a_benchmark_run()
        object.__setattr__(low_confidence_run, "overall_confidence", 0.1)
        decision = self.service.decide_promotion(low_confidence_run, None, min_confidence=0.6)
        self.assertEqual(decision.decision, JudgeDecision.INCONCLUSIVE)

    def test_no_champion_and_no_history_is_a_pass_nothing_to_regress_against(self) -> None:
        run = a_benchmark_run()
        decision = self.service.decide_promotion(run, None, min_confidence=0.6)
        self.assertEqual(decision.decision, JudgeDecision.PASS)


class TestHistoricalBestComparison(unittest.TestCase):
    """Lettres O/U du §19 — « candidate vs champion vs historical best » ;
    une régression vs le PIC historique reste visible même si le candidat
    ne régresse pas vs le champion COURANT (qui a pu lui-même déjà régressé)."""

    def test_candidate_below_historical_peak_is_visible_even_if_not_below_current_champion(self) -> None:
        factory = sqlite_session_factory()
        genome_id = promote_a_genome(factory)
        runtime = AgentRuntime(factory)
        service = CapabilityBenchmarkService(factory, agent_runtime=runtime)
        limits = a_limits()

        # Un premier run "historique" avec un score parfait, jamais égalé ensuite.
        first = service.run_benchmark(
            specialty="research", member_id="peak", generation=0, genome_version_id=genome_id,
            genome_content=None, limits=limits, compared_champion_id=None,
        )
        self.assertIsNotNone(first)
        best = service.get_historical_best("research")
        self.assertIn(JudgeDimension.CORRECTNESS, best)

        second = service.run_benchmark(
            specialty="research", member_id="later", generation=1, genome_version_id=genome_id,
            genome_content=None, limits=limits, compared_champion_id="peak",
        )
        self.assertIsNotNone(second)
        delta_vs_best = service.compute_delta_vs_historical_best(second)
        self.assertEqual(delta_vs_best.reference_kind, "historical_best")
        self.assertIsNone(delta_vs_best.reference_run_id)  # §9 : pas un run unique, honnêtement None.


class TestDeterminismAndOrdering(unittest.TestCase):
    """Lettres P/Q/R/V du §19 — même agrégation stricte que CHEAP/FULL
    (N+8, `_aggregate`, réutilisée par `GauntletRunner.run_stage`)."""

    def test_same_judge_results_in_different_order_produce_the_same_decision(self) -> None:
        results = (
            a_judge_result(JudgeDimension.CORRECTNESS, JudgeDecision.PASS),
            a_judge_result(JudgeDimension.SECURITY, JudgeDecision.PASS),
            a_judge_result(JudgeDimension.EFFICIENCY, JudgeDecision.REJECT, score=0.0),
        )
        runner = GauntletRunner()
        forward = runner.run_stage(results, stage=GauntletStage.BENCHMARK, specialty="research", generation=1, member_id="m1")
        backward = runner.run_stage(
            tuple(reversed(results)), stage=GauntletStage.BENCHMARK, specialty="research", generation=1, member_id="m1",
        )
        self.assertEqual(forward.decision, backward.decision)
        self.assertEqual(forward.overall_score, backward.overall_score)

    def test_disagreement_flag_is_set_when_judges_disagree(self) -> None:
        """Lettre R du §19 — fondation de calibration des juges (§16) :
        le désaccord reste observable, jamais masqué."""
        results = (
            a_judge_result(JudgeDimension.CORRECTNESS, JudgeDecision.PASS),
            a_judge_result(JudgeDimension.SECURITY, JudgeDecision.REJECT, score=0.0),
        )
        result = GauntletRunner().run_stage(
            results, stage=GauntletStage.BENCHMARK, specialty="research", generation=1, member_id="m1",
        )
        self.assertTrue(result.disagreement)

    def test_no_single_judge_dominance_a_lone_pass_never_overturns_a_reject(self) -> None:
        """Lettre V du §19."""
        results = (
            a_judge_result(JudgeDimension.CORRECTNESS, JudgeDecision.PASS),
            a_judge_result(JudgeDimension.SECURITY, JudgeDecision.REJECT, score=0.0),
            a_judge_result(JudgeDimension.EFFICIENCY, JudgeDecision.REJECT, score=0.0),
        )
        result = GauntletRunner().run_stage(
            results, stage=GauntletStage.BENCHMARK, specialty="research", generation=1, member_id="m1",
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)

    def test_a_single_critical_veto_blocks_regardless_of_every_other_pass(self) -> None:
        results = (
            *(
                a_judge_result(d, JudgeDecision.PASS)
                for d in CAPABILITY_DIMENSIONS if d is not JudgeDimension.SECURITY
            ),
            a_judge_result(JudgeDimension.SECURITY, JudgeDecision.REJECT, score=0.0, veto=True),
        )
        result = GauntletRunner().run_stage(
            results, stage=GauntletStage.BENCHMARK, specialty="research", generation=1, member_id="m1",
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)


if __name__ == "__main__":
    unittest.main()
