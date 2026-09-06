"""Tests du pont Judge -> LearningFeedback -> Weakness -> Curriculum
(passe « Judge Feedback -> Weakness -> Curriculum -> Adaptive Evolution »,
N+9) — classification (`apex.metalearner.judge_feedback_service`),
agrégation (`apex.metalearner.weakness_engine`), recommandations
(`apex.metalearner.curriculum_engine`). Lettres du §13 de la mission
citées explicitement dans chaque docstring de test.

Même sandbox SQLite en mémoire que `tests/test_evolution_service.py` —
persistance RÉELLE (`JudgeLearningSignalRepository`), jamais un mock de la
couche DB elle-même."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apex.db.engine import build_session_factory
from apex.db.models import Base
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.metalearner.judge_feedback_model import (
    FailureCategory,
    ImprovementStatus,
    JudgeLearningSignal,
    SignalCategory,
)
from apex.metalearner.judge_feedback_repository import JudgeLearningSignalRepository
from apex.metalearner.judge_feedback_service import JudgeFeedbackService, classify
from apex.metalearner.judge_model import (
    GauntletResult,
    GauntletStage,
    JudgeDecision,
    JudgeDimension,
    JudgeEvidence,
    JudgeFinding,
    JudgeResult,
    JudgeSeverity,
)
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus


def sqlite_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def an_eval_run(verdict: OracleVerdict = OracleVerdict.PASS, *, eval_id: str = "run-1") -> EvalRun:
    run = EvalRun.new(
        task_id="task-1", method=EvalMethod.PRACTICE, oracle_verdict=verdict, oracle_detail="",
        exit_code=0, elapsed_s=1.0, facts={}, created_by="core", sandbox_status=ExecutionStatus.SUCCESS,
    )
    run.id = eval_id
    return run


def a_judge_result(
    *, decision: JudgeDecision = JudgeDecision.PASS, dimension: JudgeDimension = JudgeDimension.CORRECTNESS,
    severity: JudgeSeverity | None = None, eval_run_id: str | None = "run-1", evidence_kind: str = "eval_run",
) -> JudgeResult:
    findings = () if severity is None else (
        JudgeFinding(dimension=dimension, severity=severity, summary="finding de test"),
    )
    evidence = () if eval_run_id is None else (JudgeEvidence(kind=evidence_kind, reference=eval_run_id),)
    return JudgeResult(
        judge_id=dimension.value, judge_version="1", dimension=dimension, decision=decision,
        score=1.0 if decision is JudgeDecision.PASS else 0.0, confidence=1.0,
        rationale="rationale de test", findings=findings, evidence=evidence,
    )


def a_gauntlet_result(
    *, member_id: str = "m1", specialty: str = "research", generation: int = 0,
    judge_results: tuple[JudgeResult, ...] = (), decision: JudgeDecision = JudgeDecision.PASS,
) -> GauntletResult:
    return GauntletResult.new(
        specialty=specialty, generation=generation, member_id=member_id, stage=GauntletStage.CHEAP,
        decision=decision, overall_score=1.0, overall_confidence=1.0, veto=False, veto_dimension=None,
        disagreement=False, stopped_after_critical_failure=False, judge_results=judge_results,
    )


class TestClassifyBasicCases(unittest.TestCase):
    """Lettres A-D du §13."""

    def test_case_a_pass_is_success(self) -> None:
        result = a_judge_result(decision=JudgeDecision.PASS)
        category, failure_category = classify(result, an_eval_run(OracleVerdict.PASS))
        self.assertEqual(category, SignalCategory.SUCCESS)
        self.assertIsNone(failure_category)

    def test_case_b_noncritical_reject_is_weakness(self) -> None:
        result = a_judge_result(decision=JudgeDecision.REJECT, severity=JudgeSeverity.HIGH)
        category, failure_category = classify(result, an_eval_run(OracleVerdict.FAIL))
        self.assertEqual(category, SignalCategory.WEAKNESS)
        self.assertEqual(failure_category, FailureCategory.GENUINE_CAPABILITY_WEAKNESS)

    def test_case_c_critical_finding_is_critical_failure(self) -> None:
        result = a_judge_result(decision=JudgeDecision.REJECT, severity=JudgeSeverity.CRITICAL)
        self.assertTrue(result.veto)
        category, failure_category = classify(result, an_eval_run(OracleVerdict.FAIL))
        self.assertEqual(category, SignalCategory.CRITICAL_FAILURE)
        self.assertEqual(failure_category, FailureCategory.GENUINE_CAPABILITY_WEAKNESS)

    def test_case_d_inconclusive_is_never_a_negative_signal(self) -> None:
        result = a_judge_result(decision=JudgeDecision.INCONCLUSIVE, eval_run_id=None)
        category, _ = classify(result, None)
        self.assertEqual(category, SignalCategory.INCONCLUSIVE)
        self.assertNotIn(category, (SignalCategory.WEAKNESS, SignalCategory.CRITICAL_FAILURE))


class TestClassifyFailureAttribution(unittest.TestCase):
    """Lettre E/F du §13 — §11 de la mission : ne jamais confondre une
    panne d'évaluation avec une faiblesse du candidat."""

    def test_case_e_judge_crash_is_judge_failure_never_candidate_failure(self) -> None:
        result = JudgeResult(
            judge_id="correctness", judge_version="1", dimension=JudgeDimension.CORRECTNESS,
            decision=JudgeDecision.INCONCLUSIVE, score=None, confidence=0.0,
            rationale="judge_error: RuntimeError: boom",
            findings=(JudgeFinding(dimension=JudgeDimension.CORRECTNESS, severity=JudgeSeverity.HIGH, summary="le juge a levé une exception"),),
            evidence=(JudgeEvidence(kind="judge_error", reference="correctness", detail="RuntimeError: boom"),),
        )
        category, failure_category = classify(result, None)
        self.assertEqual(category, SignalCategory.INCONCLUSIVE)
        self.assertEqual(failure_category, FailureCategory.JUDGE_FAILURE)
        self.assertNotEqual(failure_category, FailureCategory.GENUINE_CAPABILITY_WEAKNESS)

    def test_case_f_infrastructure_error_is_infrastructure_failure(self) -> None:
        result = a_judge_result(decision=JudgeDecision.INCONCLUSIVE)
        category, failure_category = classify(result, an_eval_run(OracleVerdict.ERROR))
        self.assertEqual(category, SignalCategory.INCONCLUSIVE)
        self.assertEqual(failure_category, FailureCategory.INFRASTRUCTURE_FAILURE)
        self.assertNotEqual(failure_category, FailureCategory.GENUINE_CAPABILITY_WEAKNESS)

    def test_timeout_or_unknown_without_error_is_insufficient_evidence(self) -> None:
        for verdict in (OracleVerdict.TIMEOUT, OracleVerdict.UNKNOWN):
            result = a_judge_result(decision=JudgeDecision.INCONCLUSIVE)
            _, failure_category = classify(result, an_eval_run(verdict))
            self.assertEqual(failure_category, FailureCategory.INSUFFICIENT_EVIDENCE, verdict)


class TestClassifyDeterminism(unittest.TestCase):
    def test_case_s_same_input_classified_twice_is_identical(self) -> None:
        result = a_judge_result(decision=JudgeDecision.REJECT, severity=JudgeSeverity.CRITICAL)
        run = an_eval_run(OracleVerdict.FAIL)
        first = classify(result, run)
        second = classify(result, run)
        self.assertEqual(first, second)


class TestJudgeFeedbackServicePersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.service = JudgeFeedbackService(self.factory)

    def test_record_signals_persists_one_row_per_judge_result(self) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EvalRunRepository as _DbEvalRunRepo

        with session_scope(self.factory) as session:
            _DbEvalRunRepo(session).create(
                id="run-1", task_id="task-1", clone_id=None, method="practice", seed=None,
                dataset_ref=None, experiment_ref=None, oracle_verdict="pass", oracle_detail=None,
                sandbox_status="success", exit_code=0, elapsed_s=1.0, facts={}, created_by="core",
                started_at=None, completed_at=None, created_at=datetime.now(UTC),
            )

        gauntlet_result = a_gauntlet_result(judge_results=(a_judge_result(decision=JudgeDecision.PASS),))
        signals = self.service.record_signals(gauntlet_result, genome_version_id="genome-1")
        self.assertEqual(len(signals), 1)
        reread = self.service.get(signals[0].id)
        self.assertIsNotNone(reread)
        self.assertEqual(reread.category, SignalCategory.SUCCESS)
        self.assertEqual(reread.genome_version_id, "genome-1")

    def test_record_signals_only_persists_the_given_subset(self) -> None:
        """§4 de la mission : jamais un double comptage — un appelant qui
        passe explicitement un SOUS-ENSEMBLE (stage FULL réutilisant des
        jugements CHEAP déjà enregistrés) ne voit persister QUE ce
        sous-ensemble."""
        gauntlet_result = a_gauntlet_result(
            judge_results=(a_judge_result(eval_run_id=None), a_judge_result(eval_run_id=None, dimension=JudgeDimension.EFFICIENCY)),
        )
        signals = self.service.record_signals(
            gauntlet_result, genome_version_id="genome-1", judge_results=(gauntlet_result.judge_results[1],),
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].dimension, JudgeDimension.EFFICIENCY)


class TestWeaknessAggregation(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = sqlite_session_factory()
        self.repo = JudgeLearningSignalRepository(self.factory)

    def _persist(
        self, *, dimension, category, failure_category, generation, member_id, genome_version_id,
        severity=JudgeSeverity.HIGH,
    ) -> JudgeLearningSignal:
        signal = JudgeLearningSignal.new(
            gauntlet_result_id="g1", member_id=member_id, specialty="research", generation=generation,
            genome_version_id=genome_version_id, judge_id=dimension.value, judge_version="1",
            dimension=dimension, decision=JudgeDecision.REJECT if category != SignalCategory.SUCCESS else JudgeDecision.PASS,
            category=category, failure_category=failure_category, severity=severity, score=0.0, confidence=1.0,
            finding_summary="test", evidence_reference=None, protocol_version="1",
        )
        self.repo.create(signal)
        return signal

    def test_case_g_recurrence_increases_across_generations(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        for gen in range(3):
            self._persist(
                dimension=JudgeDimension.ROBUSTNESS, category=SignalCategory.CRITICAL_FAILURE,
                failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=gen,
                member_id=f"m{gen}", genome_version_id=f"genome{gen}",
            )
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(len(weaknesses), 1)
        self.assertEqual(weaknesses[0].recurrence_count, 3)
        self.assertEqual(weaknesses[0].frequency, 3)

    def test_case_i_aggregation_tracks_affected_generations(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.SECURITY, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m0", genome_version_id="g0")
        self._persist(dimension=JudgeDimension.SECURITY, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=2, member_id="m2", genome_version_id="g2")
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(weaknesses[0].affected_generations, (0, 2))

    def test_case_j_aggregation_tracks_affected_genomes(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.EFFICIENCY, category=SignalCategory.WEAKNESS, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m0", genome_version_id="genomeA")
        self._persist(dimension=JudgeDimension.EFFICIENCY, category=SignalCategory.WEAKNESS, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=1, member_id="m1", genome_version_id="genomeB")
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(set(weaknesses[0].affected_genomes), {"genomeA", "genomeB"})

    def test_judge_failure_and_infrastructure_signals_never_count_as_weakness(self) -> None:
        """§4/§11 : SUCCESS/FAILURE/INCONCLUSIVE n'entrent JAMAIS dans
        l'agrégation de faiblesse (WEAKNESS_SIGNAL_CATEGORIES seul)."""
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.CORRECTNESS, category=SignalCategory.INCONCLUSIVE, failure_category=FailureCategory.JUDGE_FAILURE, generation=0, member_id="m0", genome_version_id="g0")
        self._persist(dimension=JudgeDimension.CORRECTNESS, category=SignalCategory.SUCCESS, failure_category=None, generation=0, member_id="m1", genome_version_id="g1")
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(weaknesses, ())

    def test_case_h_improvement_status_recovering_then_resolved(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.ROBUSTNESS, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m0", genome_version_id="g0")
        # Une génération plus récente, SANS cette faiblesse (autre dimension) —
        # fait avancer `latest_generation` sans ajouter de récurrence.
        self._persist(dimension=JudgeDimension.CORRECTNESS, category=SignalCategory.SUCCESS, failure_category=None, generation=1, member_id="m1", genome_version_id="g1")
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(weaknesses[0].improvement_status, ImprovementStatus.RECOVERING)

        self._persist(dimension=JudgeDimension.CORRECTNESS, category=SignalCategory.SUCCESS, failure_category=None, generation=2, member_id="m2", genome_version_id="g2")
        weaknesses2 = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(weaknesses2[0].improvement_status, ImprovementStatus.RESOLVED)

    def test_persistent_when_observed_at_the_latest_generation(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.SECURITY, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=3, member_id="m3", genome_version_id="g3")
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(weaknesses[0].improvement_status, ImprovementStatus.PERSISTENT)

    def test_case_z_history_returns_full_raw_signal_stream(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.SECURITY, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m0", genome_version_id="g0")
        self._persist(dimension=JudgeDimension.CORRECTNESS, category=SignalCategory.SUCCESS, failure_category=None, generation=0, member_id="m1", genome_version_id="g1")
        history = WeaknessEngineService(self.factory).history("research")
        self.assertEqual(len(history), 2)  # inclut SUCCESS, jamais filtré à la lecture brute.
        security_only = WeaknessEngineService(self.factory).history("research", dimension=JudgeDimension.SECURITY)
        self.assertEqual(len(security_only), 1)

    def test_case_r_aggregation_order_independent(self) -> None:
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.SECURITY, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m0", genome_version_id="g0")
        self._persist(dimension=JudgeDimension.EFFICIENCY, category=SignalCategory.WEAKNESS, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m1", genome_version_id="g1")
        first = WeaknessEngineService(self.factory).aggregate("research")
        second = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(
            [(w.dimension, w.frequency) for w in first], [(w.dimension, w.frequency) for w in second],
        )

    def test_case_t_no_single_dimension_masks_another(self) -> None:
        """§9/§29 de la mission : APEX n'apprend jamais « satisfaire un
        seul juge » — un dimension SUCCESS n'efface jamais une AUTRE
        dimension WEAKNESS/CRITICAL pour le MÊME membre."""
        from apex.metalearner.weakness_engine import WeaknessEngineService

        self._persist(dimension=JudgeDimension.CORRECTNESS, category=SignalCategory.SUCCESS, failure_category=None, generation=0, member_id="m0", genome_version_id="g0")
        self._persist(dimension=JudgeDimension.SECURITY, category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS, generation=0, member_id="m0", genome_version_id="g0")
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(len(weaknesses), 1)
        self.assertEqual(weaknesses[0].dimension, JudgeDimension.SECURITY)


class TestLineageCorrelation(unittest.TestCase):
    """Lettre K du §13 — « suspected inherited weakness », jamais une
    causalité affirmée."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()

    def test_case_k_common_ancestor_across_affected_members_is_suspected(self) -> None:
        from apex.metalearner.population_service import PopulationService

        population = PopulationService(self.factory)
        ancestor = population.add_member(
            specialty="research", generation=0, clone_id="clone-a", genome_version_id="genome-a",
            parent_member_id=None, created_by="core", experiment_ref="ref-a", max_population_size=100,
        )
        child_1 = population.add_member(
            specialty="research", generation=1, clone_id="clone-b", genome_version_id="genome-b",
            parent_member_id=ancestor.id, created_by="core", experiment_ref="ref-b", max_population_size=100,
        )
        child_2 = population.add_member(
            specialty="research", generation=1, clone_id="clone-c", genome_version_id="genome-c",
            parent_member_id=ancestor.id, created_by="core", experiment_ref="ref-c", max_population_size=100,
        )

        repo = JudgeLearningSignalRepository(self.factory)
        for member in (child_1, child_2):
            signal = JudgeLearningSignal.new(
                gauntlet_result_id="g1", member_id=member.id, specialty="research", generation=member.generation,
                genome_version_id=member.genome_version_id, judge_id="security", judge_version="1",
                dimension=JudgeDimension.SECURITY, decision=JudgeDecision.REJECT,
                category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS,
                severity=JudgeSeverity.CRITICAL, score=0.0, confidence=1.0, finding_summary="test",
                evidence_reference=None, protocol_version="1",
            )
            repo.create(signal)

        from apex.metalearner.weakness_engine import WeaknessEngineService

        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertEqual(len(weaknesses), 1)
        self.assertEqual(weaknesses[0].suspected_inherited_from, ancestor.id)

    def test_single_affected_member_has_no_lineage_correlation(self) -> None:
        from apex.metalearner.population_service import PopulationService
        from apex.metalearner.weakness_engine import WeaknessEngineService

        population = PopulationService(self.factory)
        member = population.add_member(
            specialty="research", generation=0, clone_id="clone-a", genome_version_id="genome-a",
            parent_member_id=None, created_by="core", experiment_ref="ref-a", max_population_size=100,
        )
        repo = JudgeLearningSignalRepository(self.factory)
        signal = JudgeLearningSignal.new(
            gauntlet_result_id="g1", member_id=member.id, specialty="research", generation=0,
            genome_version_id=member.genome_version_id, judge_id="security", judge_version="1",
            dimension=JudgeDimension.SECURITY, decision=JudgeDecision.REJECT,
            category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS,
            severity=JudgeSeverity.CRITICAL, score=0.0, confidence=1.0, finding_summary="test",
            evidence_reference=None, protocol_version="1",
        )
        repo.create(signal)
        weaknesses = WeaknessEngineService(self.factory).aggregate("research")
        self.assertIsNone(weaknesses[0].suspected_inherited_from)


class TestCurriculumEngineHonesty(unittest.TestCase):
    """Lettres L/M du §13 — le curriculum ne fabrique jamais un résultat."""

    def setUp(self) -> None:
        self.factory = sqlite_session_factory()

    def test_case_m_security_dimension_never_fabricates_a_task_generator(self) -> None:
        from apex.metalearner.curriculum_engine import CurriculumEngineService

        repo = JudgeLearningSignalRepository(self.factory)
        signal = JudgeLearningSignal.new(
            gauntlet_result_id="g1", member_id="m1", specialty="research", generation=0,
            genome_version_id="genome-a", judge_id="security", judge_version="1",
            dimension=JudgeDimension.SECURITY, decision=JudgeDecision.REJECT,
            category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS,
            severity=JudgeSeverity.CRITICAL, score=0.0, confidence=1.0, finding_summary="test",
            evidence_reference=None, protocol_version="1",
        )
        repo.create(signal)
        targets = CurriculumEngineService(self.factory).build_targets("research")
        self.assertEqual(len(targets), 1)
        self.assertIsNone(targets[0].recommended_mode)
        self.assertEqual(targets[0].base_task_id, None)
        self.assertEqual(targets[0].focus_genome_paths, ())
        self.assertIn("aucun", targets[0].rationale.lower())

    def test_case_m_generalization_dimension_never_fabricates_a_genome_field(self) -> None:
        from apex.metalearner.curriculum_engine import CurriculumEngineService

        repo = JudgeLearningSignalRepository(self.factory)
        signal = JudgeLearningSignal.new(
            gauntlet_result_id="g1", member_id="m1", specialty="research", generation=0,
            genome_version_id="genome-a", judge_id="generalization", judge_version="1",
            dimension=JudgeDimension.GENERALIZATION, decision=JudgeDecision.REJECT,
            category=SignalCategory.CRITICAL_FAILURE, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS,
            severity=JudgeSeverity.CRITICAL, score=0.0, confidence=1.0, finding_summary="test",
            evidence_reference=None, protocol_version="1",
        )
        repo.create(signal)
        targets = CurriculumEngineService(self.factory).build_targets("research")
        self.assertEqual(targets[0].focus_genome_paths, ())

    def test_case_l_efficiency_dimension_recommends_only_a_real_genome_path(self) -> None:
        from apex.metalearner.curriculum_engine import CurriculumEngineService

        repo = JudgeLearningSignalRepository(self.factory)
        signal = JudgeLearningSignal.new(
            gauntlet_result_id="g1", member_id="m1", specialty="research", generation=0,
            genome_version_id="genome-a", judge_id="efficiency", judge_version="1",
            dimension=JudgeDimension.EFFICIENCY, decision=JudgeDecision.REJECT,
            category=SignalCategory.WEAKNESS, failure_category=FailureCategory.GENUINE_CAPABILITY_WEAKNESS,
            severity=JudgeSeverity.HIGH, score=0.0, confidence=1.0, finding_summary="test",
            evidence_reference=None, protocol_version="1",
        )
        repo.create(signal)
        targets = CurriculumEngineService(self.factory).build_targets("research")
        self.assertEqual(targets[0].focus_genome_paths, ("execution.timeout_s",))
        # apex.genome.clone_config._OVERRIDABLE_FIELDS — jamais un chemin inventé.
        from apex.genome.clone_config import _OVERRIDABLE_FIELDS

        self.assertIn("timeout_s", _OVERRIDABLE_FIELDS)


if __name__ == "__main__":
    unittest.main()
