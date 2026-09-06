"""Tests du Judge System / Gauntlet (passe « Strict Judge System +
Gauntlet Learning Loop », N+8) — juges individuels (`apex.metalearner.
judges`) et agrégation stricte, déterministe (`apex.metalearner.gauntlet`).

Principe vérifié partout ici (§2 de la mission) : « A solution does not
pass because it looks good. A solution passes because it survives hostile
evaluation. » — aucune moyenne ne doit jamais masquer un véto (§8), aucune
preuve manquante ne doit jamais devenir un `PASS` silencieux (§9/§10/§23).
"""

from __future__ import annotations

import unittest

from apex.evalrunner.model import EvalMethod, EvalRun
from apex.metalearner.gauntlet import GauntletRunner, _aggregate
from apex.metalearner.gauntlet_repository import GauntletRepository
from apex.metalearner.judge_model import (
    GAUNTLET_PROTOCOL_VERSION,
    GauntletStage,
    JudgeDecision,
    JudgeDimension,
    JudgeSeverity,
)
from apex.metalearner.judges import (
    CorrectnessJudge,
    EfficiencyJudge,
    GeneralizationJudge,
    JudgeInput,
    RegressionJudge,
    RobustnessJudge,
    SecurityJudge,
)
from apex.oracles.model import OracleVerdict
from apex.sandbox.model import ExecutionStatus, ResourceLimits


def an_eval_run(
    verdict: OracleVerdict = OracleVerdict.PASS, *, elapsed_s: float | None = 1.0,
    status: ExecutionStatus = ExecutionStatus.SUCCESS, eval_id: str = "run-1",
) -> EvalRun:
    run = EvalRun.new(
        task_id="task-1", method=EvalMethod.PRACTICE, oracle_verdict=verdict, oracle_detail="",
        exit_code=0, elapsed_s=elapsed_s, facts={}, created_by="core", sandbox_status=status,
    )
    run.id = eval_id
    return run


def limits(timeout_s: float = 10.0) -> ResourceLimits:
    return ResourceLimits(cpu_cores=1, memory_mb=512, pids_limit=32, timeout_s=timeout_s, disk_mb=64, network="none")


class TestCorrectnessJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = CorrectnessJudge()

    def test_pass_verdict_is_a_pass_with_full_confidence(self) -> None:
        result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.PASS)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.confidence, 1.0)
        self.assertFalse(result.veto)

    def test_fail_verdict_is_a_reject_never_a_veto(self) -> None:
        result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(OracleVerdict.FAIL), limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertFalse(result.veto)
        self.assertEqual(result.findings[0].severity, JudgeSeverity.HIGH)

    def test_infra_verdicts_are_inconclusive_never_guessed(self) -> None:
        for verdict in (OracleVerdict.ERROR, OracleVerdict.TIMEOUT, OracleVerdict.UNKNOWN):
            result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(verdict), limits=limits(), candidate_fitness_components={}))
            self.assertEqual(result.decision, JudgeDecision.INCONCLUSIVE, verdict)
            self.assertIsNone(result.score)
            self.assertEqual(result.confidence, 0.0)

    def test_evidence_cites_the_real_eval_run_id(self) -> None:
        result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(eval_id="eval-xyz"), limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.evidence[0].reference, "eval-xyz")


class TestEfficiencyJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = EfficiencyJudge()

    def test_low_cost_is_a_pass(self) -> None:
        run = an_eval_run(elapsed_s=1.0)
        result = self.judge.evaluate(JudgeInput(eval_run=run, limits=limits(10.0), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.PASS)

    def test_cost_above_threshold_is_a_reject(self) -> None:
        run = an_eval_run(elapsed_s=9.6)
        result = self.judge.evaluate(
            JudgeInput(eval_run=run, limits=limits(10.0), candidate_fitness_components={}, efficiency_reject_threshold=0.9),
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertFalse(result.veto)  # jamais un véto — une question d'efficacité, pas de sécurité.

    def test_resource_limit_exceeded_is_a_reject_not_a_security_matter(self) -> None:
        run = an_eval_run(status=ExecutionStatus.OOM_KILLED)
        result = self.judge.evaluate(JudgeInput(eval_run=run, limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.REJECT)

    def test_unknown_duration_is_inconclusive(self) -> None:
        run = an_eval_run(elapsed_s=None)
        result = self.judge.evaluate(JudgeInput(eval_run=run, limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.INCONCLUSIVE)


class TestSecurityJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = SecurityJudge()

    def test_no_sandbox_bypass_attempt_is_a_pass(self) -> None:
        result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.PASS)

    def test_network_denied_is_a_critical_veto(self) -> None:
        run = an_eval_run(status=ExecutionStatus.NETWORK_DENIED)
        result = self.judge.evaluate(JudgeInput(eval_run=run, limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)
        self.assertEqual(result.findings[0].severity, JudgeSeverity.CRITICAL)

    def test_permission_denied_is_a_critical_veto(self) -> None:
        run = an_eval_run(status=ExecutionStatus.PERMISSION_DENIED)
        result = self.judge.evaluate(JudgeInput(eval_run=run, limits=limits(), candidate_fitness_components={}))
        self.assertTrue(result.veto)

    def test_resource_limit_exceeded_is_not_a_security_matter(self) -> None:
        run = an_eval_run(status=ExecutionStatus.OOM_KILLED)
        result = self.judge.evaluate(JudgeInput(eval_run=run, limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.PASS)  # EfficiencyJudge's problem, not Security's.


class TestRegressionJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = RegressionJudge()

    def test_no_champion_is_a_trivial_pass(self) -> None:
        result = self.judge.evaluate(
            JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={"quality": 1.0}),
        )
        self.assertEqual(result.decision, JudgeDecision.PASS)

    def test_quality_regression_against_champion_is_a_critical_veto(self) -> None:
        """Case D du §22 : le champion résolvait la tâche, le candidat non
        — une régression de correction est toujours un véto, jamais
        masquée par une moyenne."""
        result = self.judge.evaluate(
            JudgeInput(
                eval_run=an_eval_run(), limits=limits(),
                candidate_fitness_components={"quality": 0.0}, champion_member_id="champ-1",
                champion_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True},
            ),
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)

    def test_major_cost_regression_is_a_reject_not_a_veto(self) -> None:
        result = self.judge.evaluate(
            JudgeInput(
                eval_run=an_eval_run(), limits=limits(),
                candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.8, "cost_known": True},
                champion_member_id="champ-1", major_regression_factor=2.0,
                champion_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True},
            ),
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertFalse(result.veto)

    def test_no_regression_is_a_pass(self) -> None:
        result = self.judge.evaluate(
            JudgeInput(
                eval_run=an_eval_run(), limits=limits(),
                candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True},
                champion_member_id="champ-1",
                champion_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True},
            ),
        )
        self.assertEqual(result.decision, JudgeDecision.PASS)


class TestRobustnessJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = RobustnessJudge()

    def test_no_repeat_performed_is_inconclusive(self) -> None:
        result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.INCONCLUSIVE)

    def test_matching_verdicts_across_two_runs_is_a_pass(self) -> None:
        result = self.judge.evaluate(
            JudgeInput(
                eval_run=an_eval_run(OracleVerdict.PASS, eval_id="a"), limits=limits(),
                candidate_fitness_components={}, repeated_eval_run=an_eval_run(OracleVerdict.PASS, eval_id="b"),
            ),
        )
        self.assertEqual(result.decision, JudgeDecision.PASS)

    def test_mismatched_verdicts_is_a_critical_veto(self) -> None:
        """§7/§16 : un candidat non-déterministe n'est jamais robuste."""
        result = self.judge.evaluate(
            JudgeInput(
                eval_run=an_eval_run(OracleVerdict.PASS, eval_id="a"), limits=limits(),
                candidate_fitness_components={}, repeated_eval_run=an_eval_run(OracleVerdict.FAIL, eval_id="b"),
            ),
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)

    def test_infra_failure_on_either_run_is_inconclusive_not_a_veto(self) -> None:
        result = self.judge.evaluate(
            JudgeInput(
                eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(),
                candidate_fitness_components={}, repeated_eval_run=an_eval_run(OracleVerdict.TIMEOUT),
            ),
        )
        self.assertEqual(result.decision, JudgeDecision.INCONCLUSIVE)


class TestGeneralizationJudge(unittest.TestCase):
    def setUp(self) -> None:
        self.judge = GeneralizationJudge()

    def test_no_holdout_performed_is_inconclusive(self) -> None:
        result = self.judge.evaluate(JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={}))
        self.assertEqual(result.decision, JudgeDecision.INCONCLUSIVE)

    def test_holdout_pass_is_a_pass(self) -> None:
        result = self.judge.evaluate(
            JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={}, holdout_eval_run=an_eval_run(OracleVerdict.PASS)),
        )
        self.assertEqual(result.decision, JudgeDecision.PASS)

    def test_holdout_fail_is_a_critical_veto(self) -> None:
        """Case F du §22 : bat le champion en validation normale mais
        échoue sur HELDOUT -> REJECT, jamais promu."""
        result = self.judge.evaluate(
            JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={}, holdout_eval_run=an_eval_run(OracleVerdict.FAIL)),
        )
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)


class TestAggregatorStrictCases(unittest.TestCase):
    """§21/§22 de la mission — cases A à I, nommées explicitement."""

    def setUp(self) -> None:
        self.runner = GauntletRunner(min_confidence_threshold=0.6)

    def _cheap(self, judge_input: JudgeInput):
        return self.runner.run_cheap(judge_input, specialty="research", generation=0, member_id="m1")

    def test_case_a_one_critical_finding_among_many_passes_rejects_overall(self) -> None:
        ji = JudgeInput(
            eval_run=an_eval_run(status=ExecutionStatus.NETWORK_DENIED), limits=limits(),
            candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.05, "cost_known": True},
        )
        result = self._cheap(ji)
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)
        self.assertEqual(result.veto_dimension, JudgeDimension.SECURITY)

    def test_case_b_high_score_low_confidence_is_inconclusive(self) -> None:
        """Score global élevé mais confiance faible (un juge INCONCLUSIVE
        parmi d'autres PASS) -> jamais un PASS silencieux."""
        ji = JudgeInput(
            eval_run=an_eval_run(elapsed_s=None), limits=limits(),  # EfficiencyJudge -> INCONCLUSIVE
            candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.05, "cost_known": True},
        )
        result = self._cheap(ji)
        self.assertEqual(result.decision, JudgeDecision.INCONCLUSIVE)

    def test_case_c_two_pass_one_critical_with_objective_evidence_rejects(self) -> None:
        ji = JudgeInput(
            eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(),
            candidate_fitness_components={"quality": 0.0},  # RegressionJudge veto vs champion
            champion_member_id="champ", champion_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True},
        )
        result = self._cheap(ji)
        self.assertEqual(result.decision, JudgeDecision.REJECT)
        self.assertTrue(result.veto)
        self.assertEqual(result.veto_dimension, JudgeDimension.REGRESSION)

    def test_case_d_slightly_better_average_but_major_regression_rejects(self) -> None:
        ji = JudgeInput(
            eval_run=an_eval_run(OracleVerdict.PASS, elapsed_s=8.0), limits=limits(10.0),
            candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.8, "cost_known": True},
            champion_member_id="champ", major_regression_factor=2.0,
            champion_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True},
        )
        result = self._cheap(ji)
        self.assertEqual(result.decision, JudgeDecision.REJECT)

    def test_case_e_happy_path_passes_but_fails_on_repeated_execution_rejects(self) -> None:
        """« fonctionne sur happy path mais échoue en robustesse » — testé
        via le stage FULL (repeated execution), l'équivalent réel
        disponible aujourd'hui d'un test « edge case »."""
        cheap_ji = JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={"quality": 1.0})
        cheap_result = self._cheap(cheap_ji)
        full_ji = JudgeInput(
            eval_run=an_eval_run(OracleVerdict.PASS, eval_id="a"), limits=limits(),
            candidate_fitness_components={"quality": 1.0}, repeated_eval_run=an_eval_run(OracleVerdict.FAIL, eval_id="b"),
        )
        full_result = self.runner.run_full(
            full_ji, specialty="research", generation=0, member_id="m1", cheap_result=cheap_result,
            compared_champion_id=None,
        )
        self.assertEqual(full_result.decision, JudgeDecision.REJECT)
        self.assertTrue(full_result.veto)

    def test_case_f_beats_champion_normally_but_fails_holdout_rejects(self) -> None:
        cheap_ji = JudgeInput(
            eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(),
            candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.05, "cost_known": True},
            champion_member_id="champ", champion_fitness_components={"quality": 1.0, "cost_penalty": 0.2, "cost_known": True},
        )
        cheap_result = self._cheap(cheap_ji)
        self.assertEqual(cheap_result.decision, JudgeDecision.PASS)  # bat le champion normalement
        full_ji = JudgeInput(
            eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(),
            candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.05, "cost_known": True},
            repeated_eval_run=an_eval_run(OracleVerdict.PASS), holdout_eval_run=an_eval_run(OracleVerdict.FAIL),
            champion_member_id="champ", champion_fitness_components={"quality": 1.0, "cost_penalty": 0.2, "cost_known": True},
        )
        full_result = self.runner.run_full(
            full_ji, specialty="research", generation=0, member_id="m1", cheap_result=cheap_result,
            compared_champion_id="champ",
        )
        self.assertEqual(full_result.decision, JudgeDecision.REJECT)
        self.assertTrue(full_result.veto)
        self.assertEqual(full_result.veto_dimension, JudgeDimension.GENERALIZATION)

    def test_case_g_judge_order_never_changes_the_decision(self) -> None:
        results_a = (
            CorrectnessJudge().evaluate(JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={})),
            SecurityJudge().evaluate(JudgeInput(eval_run=an_eval_run(status=ExecutionStatus.NETWORK_DENIED), limits=limits(), candidate_fitness_components={})),
            EfficiencyJudge().evaluate(JudgeInput(eval_run=an_eval_run(), limits=limits(), candidate_fitness_components={})),
        )
        results_b = tuple(reversed(results_a))
        agg_a = _aggregate(results_a, min_confidence_threshold=0.6)
        agg_b = _aggregate(results_b, min_confidence_threshold=0.6)
        self.assertEqual(agg_a, agg_b)

    def test_case_h_same_input_evaluated_twice_is_deterministic(self) -> None:
        ji = JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True})
        first = self._cheap(ji)
        second = self.runner.run_cheap(ji, specialty="research", generation=0, member_id="m2")
        self.assertEqual(first.decision, second.decision)
        self.assertEqual(first.overall_score, second.overall_score)
        self.assertEqual(first.veto, second.veto)

    def test_case_i_a_judge_that_raises_never_silently_becomes_a_pass(self) -> None:
        from unittest.mock import patch

        ji = JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True})
        with patch.object(CorrectnessJudge, "evaluate", side_effect=RuntimeError("boom")):
            result = self._cheap(ji)
        self.assertIn(result.decision, (JudgeDecision.INCONCLUSIVE, JudgeDecision.REJECT))
        self.assertNotEqual(result.decision, JudgeDecision.PASS)
        # L'erreur doit être visible dans les preuves persistées, jamais avalée.
        correctness_results = [r for r in result.judge_results if r.dimension is JudgeDimension.CORRECTNESS]
        self.assertEqual(len(correctness_results), 1)
        self.assertIn("judge_error", correctness_results[0].rationale)


class TestGauntletStageEarlyStop(unittest.TestCase):
    def test_full_stage_never_spends_expensive_judges_after_a_cheap_veto(self) -> None:
        runner = GauntletRunner(min_confidence_threshold=0.6)
        cheap_ji = JudgeInput(eval_run=an_eval_run(status=ExecutionStatus.NETWORK_DENIED), limits=limits(), candidate_fitness_components={"quality": 1.0})
        cheap_result = runner.run_cheap(cheap_ji, specialty="research", generation=0, member_id="m1")
        self.assertTrue(cheap_result.veto)
        full_result = runner.run_full(
            cheap_ji, specialty="research", generation=0, member_id="m1", cheap_result=cheap_result,
            compared_champion_id=None,
        )
        self.assertTrue(full_result.stopped_after_critical_failure)
        self.assertEqual(len(full_result.judge_results), len(cheap_result.judge_results))  # aucun juge EXPENSIVE ajouté.

    def test_disabled_expensive_judges_are_honestly_absent_never_fabricated(self) -> None:
        runner = GauntletRunner(min_confidence_threshold=0.6)
        cheap_ji = JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True})
        cheap_result = runner.run_cheap(cheap_ji, specialty="research", generation=0, member_id="m1")
        full_result = runner.run_full(
            cheap_ji, specialty="research", generation=0, member_id="m1", cheap_result=cheap_result,
            compared_champion_id=None, run_expensive=False,
        )
        dimensions = {jr.dimension for jr in full_result.judge_results}
        self.assertNotIn(JudgeDimension.ROBUSTNESS, dimensions)
        self.assertNotIn(JudgeDimension.GENERALIZATION, dimensions)


class TestProtocolVersioning(unittest.TestCase):
    def test_gauntlet_result_carries_the_current_protocol_version(self) -> None:
        runner = GauntletRunner()
        ji = JudgeInput(eval_run=an_eval_run(OracleVerdict.PASS), limits=limits(), candidate_fitness_components={"quality": 1.0, "cost_penalty": 0.1, "cost_known": True})
        result = runner.run_cheap(ji, specialty="research", generation=0, member_id="m1")
        self.assertEqual(result.protocol_version, GAUNTLET_PROTOCOL_VERSION)


class TestPersistenceRoundTrip(unittest.TestCase):
    def test_gauntlet_result_survives_a_write_read_round_trip(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        from apex.db.engine import build_session_factory
        from apex.db.models import Base

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
        Base.metadata.create_all(engine)
        factory = build_session_factory(engine)
        repo = GauntletRepository(factory)

        runner = GauntletRunner()
        ji = JudgeInput(
            eval_run=an_eval_run(status=ExecutionStatus.NETWORK_DENIED), limits=limits(),
            candidate_fitness_components={"quality": 1.0},
        )
        result = runner.run_cheap(ji, specialty="research", generation=2, member_id="member-42")
        repo.create(result)

        reread = repo.get(result.id)
        self.assertIsNotNone(reread)
        self.assertEqual(reread.decision, JudgeDecision.REJECT)
        self.assertTrue(reread.veto)
        self.assertEqual(reread.veto_dimension, JudgeDimension.SECURITY)
        self.assertEqual(reread.stage, GauntletStage.CHEAP)
        self.assertEqual(len(reread.judge_results), len(result.judge_results))
        self.assertEqual(reread.judge_results[0].judge_id, result.judge_results[0].judge_id)

        by_member = repo.list_by_member("member-42")
        self.assertEqual(len(by_member), 1)
        by_specialty = repo.list_by_specialty("research")
        self.assertEqual(len(by_specialty), 1)


if __name__ == "__main__":
    unittest.main()
