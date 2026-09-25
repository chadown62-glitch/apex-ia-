"""Tests de l'adaptateur « Generated-Solution Evaluation Loop » du
Continuous Improvement Engine — sandbox RÉEL, modèle scripté.

Ces tests vérifient des INVARIANTS du benchmark lui-même (valables avant
comme après les correctifs des cycles) : ils ne figent aucun défaut."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from apex.improvement.capabilities import generated_solution as gs
from apex.improvement.model import CycleKind
from apex.oracles.model import OracleVerdict


class TestCorpus(unittest.TestCase):
    def test_every_family_is_known_and_covered(self) -> None:
        families = {c.family for c in gs.CORPUS}
        self.assertEqual(families, {"correct", "incorrect", "invalid", "adversarial"})
        self.assertEqual(len({c.label for c in gs.CORPUS}), len(gs.CORPUS))

    def test_corpus_fingerprint_is_stable(self) -> None:
        self.assertEqual(gs.corpus_fingerprint(), gs.corpus_fingerprint())


class TestAdapter(unittest.TestCase):
    def test_every_metric_is_blocked_when_no_isolated_sandbox_exists(self) -> None:
        with patch.object(gs, "_sandbox_available", return_value=(False, "aucun sandbox")):
            snapshot = gs.GeneratedSolutionLoopAdapter().measure("v")
        self.assertEqual(snapshot.samples, {})
        self.assertEqual(set(snapshot.blocked), {m.name for m in gs.SPEC.metrics})

    def test_correct_candidates_are_accepted_by_the_real_path(self) -> None:
        env = gs._Env()
        for candidate in (c for c in gs.CORPUS if c.is_correct):
            with self.subTest(candidate=candidate.label):
                run = env.run_candidate(gs.fixed(candidate.code))
                self.assertIs(run.oracle_verdict, OracleVerdict.PASS)

    def test_plainly_wrong_candidates_are_rejected_by_the_real_path(self) -> None:
        env = gs._Env()
        for candidate in (c for c in gs.CORPUS if c.family in ("incorrect", "invalid")):
            with self.subTest(candidate=candidate.label):
                run = env.run_candidate(gs.fixed(candidate.code))
                self.assertIsNot(run.oracle_verdict, OracleVerdict.PASS)


class TestJudge(unittest.TestCase):
    def test_every_cycle_kind_has_probes(self) -> None:
        judge = gs.build_judge()
        for kind in CycleKind:
            self.assertTrue(judge.probes_for(kind), kind)

    def test_core_path_probes_do_not_refute(self) -> None:
        """Le chemin TASK->...->EXPERIENCE et la causalité expérience ->
        décision suivante existent réellement (preuve positive)."""
        self.assertFalse(gs._probe_e2e_path().refuted)
        self.assertFalse(gs._probe_experience_causality().refuted)

    def test_model_outage_is_classified_as_infrastructure(self) -> None:
        self.assertFalse(gs._probe_model_unavailable().refuted)

    def test_judge_refuses_its_own_author(self) -> None:
        judge = gs.build_judge("same")
        from apex.improvement import SelfConfirmationError

        with self.assertRaises(SelfConfirmationError):
            judge.evaluate(
                CycleKind.IMPROVEMENT,
                author_id="same",
                capability_id=gs.CAPABILITY_ID,
                cycle_index=0,
                version="v",
            )

    def test_timeout_probe_classifies_infinite_loop(self) -> None:
        outcome = gs._probe_timeout()
        self.assertFalse(outcome.refuted, outcome.observed)
        self.assertIn("timeout", outcome.observed)


if __name__ == "__main__":
    unittest.main()
