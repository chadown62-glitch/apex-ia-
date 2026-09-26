"""Cycle 2 de la capacité CODE REPAIR : attaques. Chaque test joue un
candidat, un exécuteur, un registre ou un environnement malhonnête contre le
VRAI code (sandbox réel, entraînement réel) et vérifie que la fraude échoue."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.capabilities.code_repair import capability as code_capability
from apex.capabilities.code_repair.benchmark import suite_name
from apex.capabilities.code_repair.corpus import ALL
from apex.capabilities.code_repair.harness import ExecutionInvalid, judge_run, run_sources
from apex.capabilities.code_repair.solver import METRIC
from apex.capabilities.registry import sandbox_provider
from apex.errors import IsolationUnavailable
from apex.neural.evaluation import DocScore, EvalReport
from apex.neural.loop.execute import ExecutionResult, execute
from apex.neural.loop.failure import FailureCause, analyze_judgement
from apex.neural.loop.judge import IndependentJudge, JudgeResult
from apex.neural.loop.observe import capability_weaknesses
from apex.neural.loop.plan import Budget, ExperimentPlan, LoopHypothesis, Weakness
from apex.neural.loop.world import PRIMARY, World, WorldIntegrityError
from apex.neural.registry import ModelRegistry, Proposal
from apex.sandbox.provider import select_sandbox_provider
from apex.sandbox.result import SandboxResult
from tests import capability_support as support

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover
    SANDBOX = False
pytestmark = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")

FN = next(f for f in ALL if f.fid == "clamp")
SIGNATURE = "def clamp(value, low, high):"
INPUTS = [repr(t) for t in FN.tests]


def expected() -> tuple[str, ...]:
    (run,) = run_sources(sandbox_provider(), name="clamp", sources=[FN.source], inputs=INPUTS)
    assert run.outputs is not None
    return run.outputs


# Candidats malhonnêtes -------------------------------------------------------------------------


def test_a_candidate_cannot_forge_its_own_success() -> None:
    want = expected()
    forged_line = "APEX_REPAIR_RESULT:deadbeef:" + json.dumps([{"status": "ok", "outputs": list(want)}])
    attacks = {
        "prints a forged result line": f"print({forged_line!r})\n{SIGNATURE}\n    return 0\n",
        "exits before the tests": f"import os\nos._exit(0)\n{SIGNATURE}\n    return value\n",
        "writes forged outputs on the result pipe": (
            "import os, sys\nos.write(int(sys.argv[1]), b'{\"outputs\": []}')\n"
            "def clamp(value, low, high):\n    return value\n"
        ),
        "raises SystemExit inside the call": "def clamp(value, low, high):\n    raise SystemExit(0)\n",
    }
    runs = run_sources(sandbox_provider(), name="clamp", sources=list(attacks.values()), inputs=INPUTS)
    for (label, _), run in zip(attacks.items(), runs, strict=True):
        assert not judge_run(run, want).passed, label


def test_expected_outputs_never_enter_the_sandbox() -> None:
    fn = next(f for f in ALL if f.fid == "escape_html")
    real = sandbox_provider()
    seen: list[str] = []

    class Spy:
        def detect(self) -> Any:
            return real.detect()

        def execute(self, spec: Any, **kw: Any) -> SandboxResult:
            seen.append(" ".join(spec.command))
            return real.execute(spec, **kw)

    inputs = [repr(t) for t in fn.tests]
    (ref,) = run_sources(real, name="escape_html", sources=[fn.source], inputs=inputs)
    assert ref.outputs is not None
    buggy = fn.source.replace('"<": "&lt;"', '"<": "&gt;"')
    run_sources(Spy(), name="escape_html", sources=[buggy], inputs=inputs)  # type: ignore[arg-type]
    assert seen and not any(out in seen[0] for out in ref.outputs if "&lt;" in out)


def test_partial_or_forged_sandbox_output_is_invalid() -> None:
    real = sandbox_provider()

    def provider_returning(stdout_of: Any) -> Any:
        class Fake:
            def detect(self) -> Any:
                return real.detect()

            def execute(self, spec: Any, **kw: Any) -> SandboxResult:
                honest = real.execute(spec, **kw)
                return replace(honest, stdout=stdout_of(honest.stdout))

        return Fake()

    two = [FN.source, FN.source]
    for tamper in (
        # nonce deviné, de même longueur, charge utile intacte : seule la vérification du nonce refuse
        lambda out: re.sub(
            r"APEX_REPAIR_RESULT:[0-9a-f]{16}:", "APEX_REPAIR_RESULT:" + "0" * 16 + ":", out
        ),
        lambda out: out + "\n" + out,  # deux lignes de résultat
        lambda out: out.rsplit("},", 1)[0] + "}]",  # un résultat manquant (exécution partielle)
        lambda out: "",  # aucune ligne
    ):
        with pytest.raises(ExecutionInvalid):
            run_sources(provider_returning(tamper), name="clamp", sources=two, inputs=INPUTS)


# Exécuteur malhonnête ----------------------------------------------------------------------------


def _plan(world: World, target: str) -> ExperimentPlan:
    weakness = Weakness(target, "code", 0.9, 0.25, 0.65, 5, "mesure")
    hyp = LoopHypothesis(
        weakness,
        "code améliore la réparation",
        "mix code",
        target,
        "decrease",
        tuple(sorted(world.suite_names)),
        "réfutée si pas de gain significatif",
        "bootstrap",
    )
    return ExperimentPlan(
        world.fingerprint,
        {"name": "init", "artifact": None},
        None,
        support.recipe(),
        hyp,
        Budget(200, 30_000, 300.0),
    )


class Bench:
    def __init__(self, root: Path) -> None:
        self.world = support.world()
        self.root = root
        self.store = LocalFilesystemArtifactStore(root=str(root / "artifacts"))
        self.judge = IndependentJudge(self.world, self.store)
        self.plan = _plan(self.world, suite_name("heldout"))
        self.honest: ExecutionResult = execute(self.plan, self.world, self.store, root / "honest")

    def verdict(self, *, replay: str | None, **over: Any) -> JudgeResult:
        kwargs: dict[str, Any] = {
            "candidate_artifact": self.honest.artifact,
            "claimed_reports": self.honest.claimed_reports,
            "training_candidate_ids": self.honest.training_candidate_ids,
            "baseline_artifact": None,
            "baseline_seed": self.plan.recipe.seed,
            "plan": self.plan,
            "replay_dir": (self.root / replay) if replay else None,
        }
        kwargs.update(over)
        return self.judge.judge(**kwargs)


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Bench:
    return Bench(tmp_path_factory.mktemp("capadv"))


def test_honest_candidate_is_measured_by_execution_and_passes_checks(bench: Bench) -> None:
    result = bench.verdict(replay="replay-honest")
    assert all(c["ok"] for c in result.checks.values()), result.checks
    assert result.primary == suite_name("heldout")  # cible pré-enregistrée = suite d'acceptation
    assert set(result.task_changes) >= {suite_name("heldout"), suite_name("transfer")}
    details = result.candidate_details[suite_name("heldout")]
    assert details and all(a["executed"] for t in details for a in t["attempts"])


def test_forged_repair_scores_are_rejected(bench: Bench) -> None:
    claimed = dict(bench.honest.claimed_reports)
    real = claimed[suite_name("heldout")]
    claimed[suite_name("heldout")] = replace(
        real, scores=tuple(DocScore(s.doc_id, 0.0, 1.0, s.n_bytes, s.n_tokens) for s in real.scores)
    )
    result = bench.verdict(replay=None, claimed_reports=claimed)
    assert not result.accepted and not result.checks["claimed_metrics_match"]["ok"]
    assert analyze_judgement(bench.plan, result).cause is FailureCause.METRIC_MISMATCH  # type: ignore[union-attr]


def test_training_on_heldout_solutions_is_unmasked(bench: Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exécuteur tricheur : glisse les SOLUTIONS held-out dans son flux
    d'entraînement tout en déclarant la recette honnête."""
    heldout_sources = [
        r["source"]
        for r in support.benchmark().references.values()
        if r["split"] in ("heldout", "transfer")
    ]
    honest_stream = World.token_stream

    def leaky(self: World, mix: dict[str, float], total: int, *, seed: int) -> tuple[list[int], list[str]]:
        stream, ids = honest_stream(self, mix, total, seed=seed)
        for text in heldout_sources:
            stream += self.tokenizer.encode(text, add_bos=True, add_eos=True)
        return stream, ids

    with monkeypatch.context() as m:
        m.setattr(World, "token_stream", leaky)
        cheat = execute(bench.plan, bench.world, bench.store, bench.root / "cheat")
    assert cheat.artifact != bench.honest.artifact
    result = bench.verdict(
        replay="replay-cheat",
        candidate_artifact=cheat.artifact,
        claimed_reports=cheat.claimed_reports,
        training_candidate_ids=bench.honest.training_candidate_ids,
    )
    assert not result.accepted and not result.checks["training_replayed"]["ok"]
    assert analyze_judgement(bench.plan, result).cause is FailureCause.UNDECLARED_TRAINING  # type: ignore[union-attr]


# Registre, contrat, environnement ------------------------------------------------------------------


def test_registry_never_lets_a_proposal_choose_a_non_acceptance_primary(tmp_path: Path) -> None:
    world = support.world()
    registry = ModelRegistry(
        tmp_path / "r.jsonl",
        primary=PRIMARY,
        acceptance=world.acceptance_suites,
        tolerances=world.metric_tolerances,
    )
    reports = {PRIMARY: EvalReport(PRIMARY, "fp", "m", (DocScore("a", 1.0, 0.0, 1, 1),))}
    entry = registry.propose(Proposal("x", "sha", "ds", reports, "accept", primary=suite_name("train")))
    assert entry["primary"] == PRIMARY
    entry = registry.propose(Proposal("y", "sha", "ds", reports, "accept", primary=suite_name("heldout")))
    assert entry["primary"] == suite_name("heldout")


def test_changing_the_success_criteria_changes_the_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = support.world()
    path = tmp_path / "world.json"
    world.save(path)
    lax = replace(code_capability.CONTRACT, guard_tolerance=1.0, target_value=0.99)
    monkeypatch.setattr(code_capability.CodeRepairCapability, "contract", lax)
    with pytest.raises(WorldIntegrityError):
        World.load(path)  # le contrat du monde gelé n'est plus celui du code


def test_capabilities_are_never_evaluated_without_a_real_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    import apex.capabilities.registry as registry_module

    selection = select_sandbox_provider()
    unavailable = replace(
        selection,
        report=replace(
            selection.report, namespaces_available=False, level=type(selection.report.level)("none")
        ),
    )
    monkeypatch.setattr(registry_module, "select_sandbox_provider", lambda: unavailable)
    registry_module.sandbox_provider.cache_clear()
    try:
        with pytest.raises(IsolationUnavailable):
            registry_module.sandbox_provider()
    finally:
        registry_module.sandbox_provider.cache_clear()


def test_capability_weakness_follows_the_contract_target() -> None:
    world = support.world()

    def reports(value: float) -> dict[str, EvalReport]:
        scores = tuple(DocScore(str(i), value, 0.0, 1, 1) for i in range(6))
        return {suite_name("heldout"): EvalReport(suite_name("heldout"), "fp", "m", scores, METRIC)}

    assert capability_weaknesses(world, reports(0.9))[0].domain == "code"
    assert capability_weaknesses(world, reports(0.2)) == []  # sous l'objectif du contrat
    bpb = {suite_name("heldout"): replace(reports(0.9)[suite_name("heldout")], metric="bits_per_byte")}
    assert capability_weaknesses(world, bpb) == []  # une autre métrique n'est pas la capacité


def test_failure_analysis_uses_the_capability_tolerance() -> None:
    world = support.world()
    plan = _plan(world, suite_name("heldout"))

    def comp(
        suite: str, metric: str, base: float, cand: float, ci: tuple[float, float], verdict: str
    ) -> dict[str, Any]:
        return {
            "suite": suite,
            "metric": metric,
            "baseline_bpb": base,
            "candidate_bpb": cand,
            "bpb_gain_ci": list(ci),
            "verdict": verdict,
            "n": 10,
        }

    def judged(transfer_after: float) -> JudgeResult:
        return JudgeResult(
            "reject",
            ["x"],
            {},
            [
                comp(suite_name("heldout"), METRIC, 0.9, 0.7, (0.1, 0.3), "significant"),
                comp(suite_name("transfer"), METRIC, 0.6, transfer_after, (-0.1, 0.05), "not_significant"),
                comp(PRIMARY, "bits_per_byte", 3.0, 3.0, (-0.01, 0.01), "not_significant"),
            ],
            primary=suite_name("heldout"),
        )

    within = analyze_judgement(plan, judged(0.63), tolerances=world.metric_tolerances)  # +0.03 < 0.05
    assert within is not None and suite_name("transfer") not in within.regressed_suites
    beyond = analyze_judgement(plan, judged(0.7), tolerances=world.metric_tolerances)  # +0.10 > 0.05
    assert beyond is not None and beyond.cause is FailureCause.PROTECTED_REGRESSION
    assert suite_name("transfer") in beyond.regressed_suites
    assert "repair_cost" in " ".join(beyond.evidence)


def test_task_view_carries_no_answer() -> None:
    inst = support.benchmark().suites[suite_name("heldout")].instances[0]
    view = json.dumps(inst.view().__dict__)
    assert inst.injected not in view
    assert all(out not in view for out in inst.expected if len(out) > 3)
    ref = support.benchmark().references[inst.fid]["source"]
    assert hashlib.sha256(ref.encode()).hexdigest() not in view and ref not in view
