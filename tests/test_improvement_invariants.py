"""Invariants du Continuous Improvement Engine : il doit être difficile à
tromper. Chaque test tente une tromperie précise et vérifie que le moteur la
refuse (exception) ou ne la convertit jamais en IMPROVED/PASS."""

from __future__ import annotations

import dataclasses
import itertools
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from apex.improvement import (
    BenchmarkSnapshot,
    CapabilitySpec,
    ContinuousImprovementEngine,
    CycleKind,
    ExternalJudge,
    FunctionProbe,
    ImplementerClaim,
    ImprovementLedger,
    ImprovementVerdict,
    MetricSpec,
    ProbeOutcome,
    compare_snapshots,
)
from apex.improvement.engine import MissingBaselineError, UnverifiedSnapshotError
from apex.improvement.ledger import LedgerIntegrityError
from apex.improvement.model import ENGINE_MEASUREMENT
from apex.improvement.remote_judge import RESULT_PREFIX, SubprocessJudge
from apex.metalearner.judge_model import JudgeDecision, JudgeDimension

REPO_ROOT = Path(__file__).resolve().parent.parent
_ALL = frozenset(CycleKind)
ACC = MetricSpec(name="acc", description="", higher_is_better=True)
FP = MetricSpec(name="fp", description="", higher_is_better=False)


def spec(*metrics: MetricSpec) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id="toy",
        name="toy",
        version="1",
        description="",
        components=(),
        dependencies=(),
        preconditions=(),
        tests=(),
        metrics=metrics or (ACC,),
        success_criteria=(),
        risks=(),
        maturity="test",
    )


class ToyAdapter:
    def __init__(self, sequence, *, metrics=(ACC,), fingerprint="v1", environment=None) -> None:
        self.spec = spec(*metrics)
        self._samples = itertools.cycle(sequence)
        self.fingerprint = fingerprint
        self.environment = environment or {"sandbox": "namespaced"}

    def benchmark_fingerprint(self) -> str:
        return self.fingerprint

    def measure(self, version: str) -> BenchmarkSnapshot:
        return BenchmarkSnapshot(
            capability_id="toy",
            version=version,
            samples=dict(next(self._samples)),
            environment=dict(self.environment),
        )


def probe(pid: str, fn) -> FunctionProbe:
    return FunctionProbe(
        probe_id=pid,
        dimension=JudgeDimension.CORRECTNESS,
        kinds=_ALL,
        description=pid,
        remediation="",
        regression_test="",
        fn=fn,
    )


def passing() -> FunctionProbe:
    return probe("ok", lambda: ProbeOutcome(refuted=False, observed="rien à réfuter"))


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = ImprovementLedger(Path(self.tmp.name) / "ledger.jsonl")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def engine(self, adapter, *probes) -> ContinuousImprovementEngine:
        return ContinuousImprovementEngine(
            adapter, ExternalJudge("judge", probes or (passing(),)), self.ledger
        )

    def cycle(self, engine, before, *, claim=ImprovementVerdict.IMPROVED, after=None):
        return engine.run_cycle(
            kind=CycleKind.IMPROVEMENT,
            cycle_index=1,
            before=before,
            version_after="v1",
            hypothesis="h",
            changes=(),
            tests_executed=(),
            claim=ImplementerClaim(author_id="implementer", summary="s", claimed_verdict=claim),
            after=after,
        )


class TestEngineRefusesDeception(_Fixture):
    def test_01_an_improvement_without_measurement_is_refused(self) -> None:
        engine = self.engine(ToyAdapter([{"acc": (0.0,)}]))
        before = engine.measure("v0")
        fabricated = BenchmarkSnapshot(
            capability_id="toy",
            version="v1",
            samples={"acc": (1.0,)},
            reproducible=True,
            definition_digest=before.definition_digest,
            environment=dict(before.environment),
            measured_by=ENGINE_MEASUREMENT,
        )
        with self.assertRaises(UnverifiedSnapshotError):
            self.cycle(engine, before, after=fabricated)
        blocked = self.engine(ToyAdapter([{}]))
        record = self.cycle(blocked, blocked.measure("v0"))
        self.assertIs(record.verdict, ImprovementVerdict.BLOCKED)

    def test_02_an_implementer_claim_alone_never_makes_an_improvement(self) -> None:
        engine = self.engine(ToyAdapter([{"acc": (1.0,)}]))
        record = self.cycle(engine, engine.measure("v0"), claim=ImprovementVerdict.IMPROVED)
        self.assertIs(record.verdict, ImprovementVerdict.UNCHANGED)
        self.assertTrue(record.claim_contradicted)

    def test_03_a_crashing_judge_is_never_a_pass(self) -> None:
        def boom() -> ProbeOutcome:
            raise RuntimeError("juge en panne")

        engine = self.engine(
            ToyAdapter([{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (1.0,)}]),
            probe("crash", boom),
        )
        record = self.cycle(engine, engine.measure("v0"))
        self.assertEqual(record.judge["decision"], JudgeDecision.INCONCLUSIVE.value)
        self.assertIs(record.verdict, ImprovementVerdict.INCONCLUSIVE)

    def test_04_a_non_reproducible_metric_is_never_an_improvement(self) -> None:
        engine = self.engine(
            ToyAdapter([{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (0.5,)}])
        )
        before = engine.measure("v0")
        record = self.cycle(engine, before)
        self.assertFalse(record.after.reproducible)
        self.assertIs(record.verdict, ImprovementVerdict.INCONCLUSIVE)

    def test_06_a_missing_baseline_is_refused(self) -> None:
        engine = self.engine(ToyAdapter([{"acc": (1.0,)}]))
        with self.assertRaises(MissingBaselineError):
            self.cycle(engine, None)
        handmade = BenchmarkSnapshot(
            capability_id="toy", version="v0", samples={"acc": (0.0,)}, reproducible=True
        )
        with self.assertRaises(UnverifiedSnapshotError):
            self.cycle(engine, handmade)

    def test_07_incompatible_environments_are_never_compared(self) -> None:
        adapter = ToyAdapter(
            [{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (1.0,)}],
            environment={"sandbox": "namespaced"},
        )
        engine = self.engine(adapter)
        before = engine.measure("v0")
        adapter.environment = {"sandbox": "containerized"}
        record = self.cycle(engine, before)
        self.assertIs(record.verdict, ImprovementVerdict.INCONCLUSIVE)
        self.assertIn("environnements incompatibles", record.verdict_reason)

    def test_08_a_benchmark_redefined_between_baseline_and_after_is_never_compared(self) -> None:
        adapter = ToyAdapter(
            [{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (1.0,)}], fingerprint="corpus-a"
        )
        engine = self.engine(adapter)
        before = engine.measure("v0")
        adapter.fingerprint = "corpus-b-plus-facile"
        record = self.cycle(engine, before)
        self.assertIs(record.verdict, ImprovementVerdict.INCONCLUSIVE)
        self.assertIn("définition du benchmark modifiée", record.verdict_reason)

    def test_08b_dropping_a_metric_changes_the_definition(self) -> None:
        before_spec, after_spec = spec(ACC, FP), spec(ACC)
        self.assertNotEqual(before_spec.definition_digest("x"), after_spec.definition_digest("x"))

    def test_09_a_regression_is_never_masked_by_another_improvement(self) -> None:
        adapter = ToyAdapter(
            [
                {"acc": (0.5,), "fp": (0.0,)},
                {"acc": (0.5,), "fp": (0.0,)},
                {"acc": (1.0,), "fp": (0.3,)},
                {"acc": (1.0,), "fp": (0.3,)},
            ],
            metrics=(ACC, FP),
        )
        engine = self.engine(adapter)
        record = self.cycle(engine, engine.measure("v0"))
        verdicts = {c.metric: c.verdict for c in record.comparisons}
        self.assertEqual(verdicts, {"acc": ImprovementVerdict.IMPROVED, "fp": ImprovementVerdict.REGRESSED})
        self.assertIs(record.verdict, ImprovementVerdict.REGRESSED)

    def test_10_inconclusive_is_never_turned_into_pass(self) -> None:
        unsure = probe("unsure", lambda: ProbeOutcome(refuted=None, observed="dépendance absente"))
        engine = self.engine(
            ToyAdapter([{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (1.0,)}]), unsure
        )
        record = self.cycle(engine, engine.measure("v0"))
        self.assertNotEqual(record.judge["decision"], JudgeDecision.PASS.value)
        self.assertIs(record.verdict, ImprovementVerdict.INCONCLUSIVE)
        verdict, _, _ = compare_snapshots(
            engine.spec,
            dataclasses.replace(record.before, reproducible=None),
            record.after,
        )
        self.assertIs(verdict, ImprovementVerdict.INCONCLUSIVE)

    def test_a_tampered_ledger_stops_every_cycle(self) -> None:
        for tamper_last in (True, False):
            with self.subTest(tamper_last=tamper_last):
                self.ledger.path.unlink(missing_ok=True)
                engine = self.engine(ToyAdapter([{"acc": (0.0,)}] * 4))
                before = engine.measure("v0")
                if not tamper_last:
                    engine.measure("v0-bis")
                self.assertEqual(self.ledger.verify(), [])
                lines = self.ledger.path.read_text(encoding="utf-8").splitlines(keepends=True)
                lines[0] = lines[0].replace('"acc": [0.0]', '"acc": [0.9]')
                self.assertIn("0.9", lines[0])
                self.ledger.path.write_text("".join(lines), encoding="utf-8")
                self.assertTrue(self.ledger.verify())
                with self.assertRaises(LedgerIntegrityError):
                    self.cycle(engine, before)


class TestUnconfirmedExperienceIsRefused(_Fixture):
    """Invariant 5 : un succès non confirmé utilisé comme succès confirmé
    est réfuté par le juge de la capacité — le cycle est FAILED."""

    @staticmethod
    def _old_behaviour():
        # `_mutate_from_experience` vit dans `apex.experience.strategy` depuis la frontière Runtime
        # (`adaptive_solution_loop` n'en garde qu'un alias importé) : on patche là où la règle l'appelle.
        from apex.experience import strategy as loop
        from apex.metalearner.experience import SuccessEvidence
        from apex.metalearner.failure_taxonomy import FailureCategory

        original = loop._mutate_from_experience

        def treat_every_success_as_confirmed(best):
            if best.failure_category is FailureCategory.SUCCESS:
                best = dataclasses.replace(best, success_evidence=SuccessEvidence.CONFIRMED)
            return original(best)

        return patch.object(loop, "_mutate_from_experience", treat_every_success_as_confirmed)

    def _probe(self) -> FunctionProbe:
        from apex.improvement.capabilities import generated_solution as gs

        return probe("unconfirmed_success_exploited", gs._probe_unconfirmed_success_exploited)

    def test_05_the_probe_passes_on_the_current_code(self) -> None:
        self.assertIs(self._probe().run().refuted, False)

    def test_05_learning_from_an_unconfirmed_success_fails_the_cycle(self) -> None:
        with self._old_behaviour():
            self.assertIs(self._probe().run().refuted, True)
            engine = self.engine(
                ToyAdapter([{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (1.0,)}]),
                self._probe(),
            )
            record = self.cycle(engine, engine.measure("v0"))
        self.assertIs(record.verdict, ImprovementVerdict.FAILED)


class TestSubprocessJudge(_Fixture):
    def _fake_python(self, body: str) -> str:
        script = Path(self.tmp.name) / "fake_python.py"
        script.write_text(textwrap.dedent(body), encoding="utf-8")
        wrapper = Path(self.tmp.name) / "fake_python.sh"
        wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        return str(wrapper)

    def _judge(self, python: str, **kwargs) -> SubprocessJudge:
        return SubprocessJudge(
            "ci-judge:generated-solution",
            capability="generated_solution",
            module_path="apex/improvement/capabilities/generated_solution.py",
            python=python,
            **kwargs,
        )

    def _assess(self, judge):
        return judge.assess(
            CycleKind.FUNCTIONALITY,
            author_id="implementer",
            capability_id="toy",
            cycle_index=1,
            version="v1",
            cycle_id="c1",
        )

    def test_a_worker_that_crashes_times_out_or_babbles_is_inconclusive(self) -> None:
        for body, kwargs in (
            ("import sys; sys.exit(3)", {}),
            ("import time; time.sleep(30)", {"timeout_s": 1.0}),
            ("print('PASS')", {}),
            (f"print({RESULT_PREFIX!r} + '{{not json')", {}),
        ):
            with self.subTest(body=body):
                assessment = self._assess(self._judge(self._fake_python(body), **kwargs))
                self.assertEqual([r.decision for r in assessment.results], [JudgeDecision.INCONCLUSIVE])
                self.assertIn("error", assessment.provenance)

    def test_a_remote_judge_cannot_return_a_global_pass_the_engine_aggregates(self) -> None:
        payload = json.dumps(
            {
                "decision": "pass",
                "results": [
                    {
                        "judge_id": "p",
                        "judge_version": "1",
                        "dimension": "correctness",
                        "decision": "inconclusive",
                        "score": None,
                        "confidence": 0.0,
                        "rationale": "?",
                        "findings": [],
                        "evidence": [],
                    }
                ],
                "findings": [],
                "provenance": {},
            }
        )
        python = self._fake_python(f"print({RESULT_PREFIX!r} + {payload!r})")
        engine = ContinuousImprovementEngine(
            ToyAdapter([{"acc": (0.0,)}, {"acc": (0.0,)}, {"acc": (1.0,)}, {"acc": (1.0,)}]),
            self._judge(python),
            self.ledger,
        )
        record = self.cycle(engine, engine.measure("v0"))
        self.assertEqual(record.judge["decision"], JudgeDecision.INCONCLUSIVE.value)
        self.assertIs(record.verdict, ImprovementVerdict.INCONCLUSIVE)
        self.assertEqual(record.judge_provenance["mode"], "subprocess")

    def test_a_pinned_judge_runs_the_definition_of_its_revision_not_the_candidate(self) -> None:
        pinned = self._judge(sys.executable, judge_ref="893d9a9")
        current = self._judge(sys.executable)
        kind = CycleKind.ADVERSARIAL

        def probe_ids(judge):
            assessment = judge.assess(
                kind, author_id="implementer", capability_id="x", cycle_index=0, version="v", cycle_id="c"
            )
            return {r.judge_id for r in assessment.results}, assessment.provenance

        pinned_ids, provenance = probe_ids(pinned)
        current_ids, _ = probe_ids(current)
        self.assertIn("timeout", pinned_ids)
        self.assertNotIn("unconfirmed_success_exploited", pinned_ids)
        self.assertEqual(current_ids - pinned_ids, {"unconfirmed_success_exploited"})
        source = subprocess.run(
            ["git", "show", "893d9a9:apex/improvement/capabilities/generated_solution.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        import hashlib

        self.assertEqual(provenance["judge_source_sha256"], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(provenance["judge_definition_differs_from_working_tree"], "oui")
        self.assertEqual(provenance["judge_ref_is_ancestor_of_head"], "oui")
        self.assertNotIn("independence", provenance)

    def test_an_option_like_judge_ref_is_refused(self) -> None:
        assessment = self._assess(self._judge(sys.executable, judge_ref="--output=/tmp/x"))
        self.assertEqual([r.decision for r in assessment.results], [JudgeDecision.INCONCLUSIVE])


if __name__ == "__main__":
    unittest.main()
