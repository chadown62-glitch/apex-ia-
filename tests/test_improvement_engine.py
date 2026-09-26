"""Tests du Continuous Improvement Engine (`apex.improvement`) — capacité
jouet, aucun système externe : ce fichier prouve les PROPRIÉTÉS du moteur
(jamais d'auto-confirmation, un véto ne se rachète pas par un gain, une
mesure non reproductible ne conclut rien, une sonde en panne n'est jamais un
PASS, hypothèse probable jamais promue confirmée sans reproduction)."""

from __future__ import annotations

import itertools
import json
import tempfile
import unittest
from pathlib import Path

from apex.improvement import (
    BenchmarkSnapshot,
    CapabilitySpec,
    ContinuousImprovementEngine,
    CycleKind,
    EvidenceLevel,
    ExternalJudge,
    FunctionProbe,
    HypothesisStatus,
    ImplementerClaim,
    ImprovementLedger,
    ImprovementVerdict,
    MetricKind,
    MetricSpec,
    ProbeOutcome,
    SelfConfirmationError,
    compare_snapshots,
)
from apex.improvement.measure import aggregate
from apex.metalearner.judge_model import JudgeDecision, JudgeDimension, JudgeSeverity

_ALL_KINDS = frozenset(CycleKind)


def a_spec(*metrics: MetricSpec) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id="toy",
        name="toy",
        version="1",
        description="capacité jouet",
        components=("toy",),
        dependencies=(),
        preconditions=(),
        tests=(),
        metrics=metrics,
        success_criteria=(),
        risks=(),
        maturity="test",
    )


def snap(
    version: str, reproducible: bool | None = True, blocked: dict | None = None, **samples
) -> BenchmarkSnapshot:
    return BenchmarkSnapshot(
        capability_id="toy",
        version=version,
        samples={k: tuple(v) for k, v in samples.items()},
        blocked=blocked or {},
        reproducible=reproducible,
        definition_digest="toy-benchmark-v1",
    )


ACC = MetricSpec(name="acc", description="", higher_is_better=True)
FP = MetricSpec(name="fp", description="", higher_is_better=False)
NOISY = MetricSpec(
    name="noisy", description="", higher_is_better=True, kind=MetricKind.STOCHASTIC, min_samples=5
)


class TestCompareSnapshots(unittest.TestCase):
    def test_deterministic_improvement_regression_and_unchanged(self) -> None:
        spec = a_spec(ACC)
        cases = [
            ((0, 1), (1, 1), ImprovementVerdict.IMPROVED),
            ((1, 1), (0, 1), ImprovementVerdict.REGRESSED),
            ((1, 0), (0, 1), ImprovementVerdict.UNCHANGED),
        ]
        for before, after, expected in cases:
            verdict, _, _ = compare_snapshots(spec, snap("a", acc=before), snap("b", acc=after))
            self.assertIs(verdict, expected)

    def test_lower_is_better_metric_direction(self) -> None:
        verdict, _, _ = compare_snapshots(a_spec(FP), snap("a", fp=(1, 1)), snap("b", fp=(0, 0)))
        self.assertIs(verdict, ImprovementVerdict.IMPROVED)

    def test_unverified_reproducibility_is_never_conclusive(self) -> None:
        verdict, _, comparisons = compare_snapshots(
            a_spec(ACC),
            snap("a", reproducible=None, acc=(0,)),
            snap("b", acc=(1,)),
        )
        self.assertIs(verdict, ImprovementVerdict.INCONCLUSIVE)
        self.assertIn("reproductibilité", comparisons[0].reason)

    def test_blocked_metric_is_blocked_not_zero(self) -> None:
        verdict, _, _ = compare_snapshots(
            a_spec(ACC),
            snap("a", blocked={"acc": "sandbox absent"}),
            snap("b", acc=(1,)),
        )
        self.assertIs(verdict, ImprovementVerdict.BLOCKED)

    def test_too_few_samples_is_inconclusive(self) -> None:
        verdict, _, _ = compare_snapshots(a_spec(NOISY), snap("a", noisy=(0, 1)), snap("b", noisy=(1, 1)))
        self.assertIs(verdict, ImprovementVerdict.INCONCLUSIVE)

    def test_stochastic_noise_is_unchanged_real_shift_is_improved(self) -> None:
        spec = a_spec(NOISY)
        noise_a, noise_b = (0, 1, 0, 1, 0, 1, 0, 1), (1, 0, 1, 0, 1, 0, 1, 0)
        verdict, _, _ = compare_snapshots(spec, snap("a", noisy=noise_a), snap("b", noisy=noise_b))
        self.assertIs(verdict, ImprovementVerdict.UNCHANGED)
        verdict, _, _ = compare_snapshots(spec, snap("a", noisy=(0,) * 8), snap("b", noisy=(1,) * 8))
        self.assertIs(verdict, ImprovementVerdict.IMPROVED)

    def test_one_regression_outweighs_any_improvement(self) -> None:
        verdict, reason, _ = compare_snapshots(
            a_spec(ACC, FP),
            snap("a", acc=(0, 0), fp=(0,)),
            snap("b", acc=(1, 1), fp=(1,)),
        )
        self.assertIs(verdict, ImprovementVerdict.REGRESSED)
        self.assertIn("fp", reason)

    def test_aggregate_of_nothing_is_inconclusive(self) -> None:
        self.assertIs(aggregate(())[0], ImprovementVerdict.INCONCLUSIVE)

    def test_foreign_capability_snapshot_is_refused(self) -> None:
        other = BenchmarkSnapshot(
            capability_id="other", version="x", samples={"acc": (1.0,)}, reproducible=True
        )
        with self.assertRaises(ValueError):
            compare_snapshots(a_spec(ACC), other, snap("b", acc=(1,)))


def probe(probe_id: str, fn, *, severity_dim=JudgeDimension.CORRECTNESS, kinds=_ALL_KINDS):
    return FunctionProbe(
        probe_id=probe_id,
        dimension=severity_dim,
        kinds=kinds,
        description=f"sonde {probe_id}",
        remediation=f"corriger {probe_id}",
        regression_test=f"test_{probe_id}",
        fn=fn,
    )


def refuted(severity=JudgeSeverity.HIGH):
    return lambda: ProbeOutcome(refuted=True, observed="défaut", hypothesis="cause X", severity=severity)


def not_refuted():
    return lambda: ProbeOutcome(refuted=False, observed="rien trouvé")


class TestExternalJudge(unittest.TestCase):
    def evaluate(self, *probes, author="implementer"):
        judge = ExternalJudge("judge", tuple(probes))
        return judge.evaluate(
            CycleKind.FUNCTIONALITY, author_id=author, capability_id="toy", cycle_index=1, version="v"
        )

    def test_all_probes_fail_to_refute_is_pass(self) -> None:
        gauntlet, _ = self.evaluate(probe("a", not_refuted()), probe("b", not_refuted()))
        self.assertIs(gauntlet.decision, JudgeDecision.PASS)

    def test_critical_refutation_vetoes_regardless_of_other_passes(self) -> None:
        gauntlet, _ = self.evaluate(
            probe("a", not_refuted()),
            probe("b", refuted(JudgeSeverity.CRITICAL), severity_dim=JudgeDimension.SECURITY),
        )
        self.assertIs(gauntlet.decision, JudgeDecision.REJECT)
        self.assertTrue(gauntlet.veto)
        self.assertIs(gauntlet.veto_dimension, JudgeDimension.SECURITY)

    def test_crashing_probe_is_inconclusive_never_pass(self) -> None:
        def boom():
            raise RuntimeError("sonde cassée")

        gauntlet, judged = self.evaluate(probe("a", not_refuted()), probe("b", boom))
        self.assertIs(gauntlet.decision, JudgeDecision.INCONCLUSIVE)
        self.assertIn("sonde cassée", judged[1].error)

    def test_inconclusive_probe_blocks_pass(self) -> None:
        gauntlet, _ = self.evaluate(
            probe("a", lambda: ProbeOutcome(refuted=None, observed="docker absent"))
        )
        self.assertIs(gauntlet.decision, JudgeDecision.INCONCLUSIVE)

    def test_author_cannot_judge_own_change(self) -> None:
        with self.assertRaises(SelfConfirmationError):
            self.evaluate(probe("a", not_refuted()), author="judge")

    def test_probes_are_filtered_by_cycle_kind(self) -> None:
        judge = ExternalJudge(
            "judge", (probe("adv", not_refuted(), kinds=frozenset({CycleKind.ADVERSARIAL})),)
        )
        with self.assertRaises(ValueError):
            judge.evaluate(
                CycleKind.FUNCTIONALITY, author_id="x", capability_id="toy", cycle_index=0, version="v"
            )

    def test_duplicate_probe_ids_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            ExternalJudge("judge", (probe("a", not_refuted()), probe("a", not_refuted())))


class _ToyAdapter:
    def __init__(self, sequence):
        self.spec = a_spec(ACC)
        self._samples = itertools.cycle(sequence)

    def benchmark_fingerprint(self) -> str:
        return "toy-benchmark-v1"

    def measure(self, version: str) -> BenchmarkSnapshot:
        return BenchmarkSnapshot(capability_id="toy", version=version, samples={"acc": next(self._samples)})


class TestEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = ImprovementLedger(Path(self.tmp.name) / "ledger.jsonl")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def engine(self, adapter, *probes) -> ContinuousImprovementEngine:
        return ContinuousImprovementEngine(adapter, ExternalJudge("judge", tuple(probes)), self.ledger)

    def claim(self, verdict=ImprovementVerdict.IMPROVED, author="implementer"):
        return ImplementerClaim(author_id=author, summary="j'ai amélioré", claimed_verdict=verdict)

    def test_measure_detects_non_reproducible_measurements(self) -> None:
        engine = self.engine(_ToyAdapter([(1.0,), (0.0,)]), probe("a", not_refuted()))
        snapshot = engine.measure("v1")
        self.assertFalse(snapshot.reproducible)
        self.assertTrue(any("NON reproductible" in n for n in snapshot.notes))

    def test_measured_gain_with_passing_judge_is_improved(self) -> None:
        engine = self.engine(_ToyAdapter([(0.0,), (0.0,), (1.0,), (1.0,)]), probe("a", not_refuted()))
        before = engine.measure("v0")
        record = engine.run_cycle(
            kind=CycleKind.FUNCTIONALITY,
            cycle_index=1,
            before=before,
            version_after="v1",
            hypothesis="h",
            changes=("c",),
            tests_executed=("t",),
            claim=self.claim(),
        )
        self.assertIs(record.verdict, ImprovementVerdict.IMPROVED)
        self.assertFalse(record.claim_contradicted)

    def test_judge_veto_turns_measured_gain_into_failed_and_contradicts_claim(self) -> None:
        engine = self.engine(
            _ToyAdapter([(0.0,), (0.0,), (1.0,), (1.0,)]), probe("sec", refuted(JudgeSeverity.CRITICAL))
        )
        record = engine.run_cycle(
            kind=CycleKind.ADVERSARIAL,
            cycle_index=2,
            before=engine.measure("v0"),
            version_after="v1",
            hypothesis="h",
            changes=(),
            tests_executed=(),
            claim=self.claim(),
        )
        self.assertIs(record.verdict, ImprovementVerdict.FAILED)
        self.assertTrue(record.claim_contradicted)
        self.assertEqual(len(record.findings), 1)
        self.assertIs(record.findings[0].status, HypothesisStatus.CONFIRMED)

    def test_measured_regression_wins_over_passing_judge(self) -> None:
        engine = self.engine(_ToyAdapter([(1.0,), (1.0,), (0.0,), (0.0,)]), probe("a", not_refuted()))
        record = engine.run_cycle(
            kind=CycleKind.IMPROVEMENT,
            cycle_index=3,
            before=engine.measure("v0"),
            version_after="v1",
            hypothesis="h",
            changes=(),
            tests_executed=(),
            claim=self.claim(),
        )
        self.assertIs(record.verdict, ImprovementVerdict.REGRESSED)

    def test_unchanged_is_never_reported_as_success(self) -> None:
        engine = self.engine(_ToyAdapter([(1.0,)]), probe("a", not_refuted()))
        record = engine.run_cycle(
            kind=CycleKind.IMPROVEMENT,
            cycle_index=3,
            before=engine.measure("v0"),
            version_after="v1",
            hypothesis="h",
            changes=(),
            tests_executed=(),
            claim=self.claim(),
        )
        self.assertIs(record.verdict, ImprovementVerdict.UNCHANGED)
        self.assertTrue(record.claim_contradicted)

    def test_flaky_refutation_is_rejected_not_confirmed(self) -> None:
        outcomes = iter(
            [ProbeOutcome(refuted=True, observed="1er run"), ProbeOutcome(refuted=False, observed="2e run")]
        )
        engine = self.engine(_ToyAdapter([(1.0,)]), probe("flaky", lambda: next(outcomes)))
        record = engine.run_cycle(
            kind=CycleKind.FUNCTIONALITY,
            cycle_index=1,
            before=engine.measure("v0"),
            version_after="v1",
            hypothesis="h",
            changes=(),
            tests_executed=(),
            claim=self.claim(ImprovementVerdict.UNCHANGED),
        )
        self.assertIs(record.findings[0].status, HypothesisStatus.REJECTED)

    def test_self_confirmation_is_refused_before_any_measurement(self) -> None:
        engine = self.engine(_ToyAdapter([(1.0,)]), probe("a", not_refuted()))
        with self.assertRaises(SelfConfirmationError):
            engine.run_cycle(
                kind=CycleKind.FUNCTIONALITY,
                cycle_index=1,
                before=snap("v0", acc=(0.0,)),
                version_after="v1",
                hypothesis="h",
                changes=(),
                tests_executed=(),
                claim=self.claim(author="judge"),
            )
        self.assertEqual(self.ledger.entries(), [])

    def test_ledger_records_snapshots_and_cycles_with_evidence_levels(self) -> None:
        engine = self.engine(_ToyAdapter([(1.0,)]), probe("a", not_refuted()))
        before = engine.measure("v0")
        engine.run_cycle(
            kind=CycleKind.FUNCTIONALITY,
            cycle_index=1,
            before=before,
            version_after="v1",
            hypothesis="h",
            changes=(),
            tests_executed=(),
            claim=self.claim(ImprovementVerdict.UNCHANGED),
        )
        self.assertEqual(len(self.ledger.snapshots("toy")), 2)
        cycles = self.ledger.cycles("toy")
        self.assertEqual(len(cycles), 1)
        levels = {e["level"] for e in cycles[0]["evidence"]}
        self.assertTrue({EvidenceLevel.MEASURED.value, EvidenceLevel.JUDGED.value}.issubset(levels))
        self.assertEqual(self.ledger.latest_snapshot("toy", "v0").version, "v0")

    def test_corrupted_ledger_line_is_reported_never_skipped(self) -> None:
        self.ledger.path.write_text(json.dumps({"type": "snapshot"}) + "\n{broken\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.ledger.entries()


if __name__ == "__main__":
    unittest.main()
