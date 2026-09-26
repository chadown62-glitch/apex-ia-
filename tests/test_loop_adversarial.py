"""Cycle 2 : attaques contre la boucle autonome. Chaque test joue un
exécuteur, un journal ou un registre malhonnête (ou un environnement
hostile) contre le VRAI code, sur un monde réel réduit et de vrais
entraînements, et vérifie que la fraude est détectée et nommée — jamais
promue."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.artifacts.model import content_hash_hex
from apex.neural.dataset import Split
from apex.neural.evaluation import DocScore, EvalReport
from apex.neural.loop.controller import DuplicateExperimentError, LoopController, LoopLimits
from apex.neural.loop.execute import ExecutionResult, StopRequested, execute
from apex.neural.loop.failure import FailureCause, analyze_judgement
from apex.neural.loop.judge import IndependentJudge, JudgeResult
from apex.neural.loop.plan import Budget, ExperimentPlan, LoopHypothesis, Recipe, Weakness
from apex.neural.loop.strategy import StrategyDecision, StrategyPolicy
from apex.neural.loop.world import PRIMARY, World
from apex.neural.registry import Proposal
from tests import loop_support as support

RECIPE = Recipe(
    init="scratch", mix={"stdlib": 1.0}, steps=20, lr=1e-2, tokens=4_000, batch_size=8, warmup_steps=5
)


def _hypothesis(world: World) -> LoopHypothesis:
    benches = tuple(sorted(world.benchmarks))
    return LoopHypothesis(
        Weakness(PRIMARY, None, 5.0, 0.0, 5.0, len(world.benchmarks[PRIMARY].documents), "initialisation"),
        "entraîner sur stdlib réduit le BPB held-out",
        "init: scratch -> entraînement",
        PRIMARY,
        "decrease",
        benches,
        "réfutée si pas de gain significatif sur held-out",
        "bootstrap",
    )


def _plan(world: World, recipe: Recipe = RECIPE, **over: object) -> ExperimentPlan:
    plan = ExperimentPlan(
        world.fingerprint,
        {"name": "init", "artifact": None},
        None,
        recipe,
        _hypothesis(world),
        Budget(200, 20_000, 120.0),
    )
    return replace(plan, **over)  # type: ignore[arg-type]


class Bench:
    """Un entraînement honnête, et de quoi le juger."""

    def __init__(self, root: Path) -> None:
        self.world = support.world()
        self.root = root
        self.store = LocalFilesystemArtifactStore(root=str(root / "artifacts"))
        self.judge = IndependentJudge(self.world, self.store)
        self.plan = _plan(self.world)
        self.honest: ExecutionResult = execute(self.plan, self.world, self.store, root / "honest")

    def verdict(self, *, replay: str | None = "replay", **over: object) -> JudgeResult:
        kwargs: dict[str, object] = {
            "candidate_artifact": self.honest.artifact,
            "claimed_reports": self.honest.claimed_reports,
            "training_candidate_ids": self.honest.training_candidate_ids,
            "baseline_artifact": None,
            "baseline_seed": self.plan.recipe.seed,
            "plan": self.plan,
            "replay_dir": (self.root / replay) if replay else None,
        }
        kwargs.update(over)
        return self.judge.judge(**kwargs)  # type: ignore[arg-type]

    def cause(self, result: JudgeResult) -> FailureCause:
        analysis = analyze_judgement(self.plan, result)
        assert analysis is not None and analysis.status == "established"
        return analysis.cause


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Bench:
    return Bench(tmp_path_factory.mktemp("adv"))


def test_honest_candidate_passes_every_check_including_replay(bench: Bench) -> None:
    result = bench.verdict(replay="replay-honest")
    assert all(c["ok"] for c in result.checks.values()), result.checks
    assert result.checks["training_replayed"]["ok"]  # rejeu bit à bit
    assert "declared_data_matches_recipe" in result.checks


def _shift(reports: dict[str, EvalReport], delta: float) -> dict[str, EvalReport]:
    return {
        n: EvalReport(
            r.benchmark,
            r.benchmark_fingerprint,
            r.model_fingerprint,
            tuple(
                DocScore(s.doc_id, s.bits_per_byte + delta, s.token_accuracy, s.n_bytes, s.n_tokens)
                for s in r.scores
            ),
        )
        for n, r in reports.items()
    }


def test_falsified_metrics_are_rejected(bench: Bench) -> None:
    result = bench.verdict(claimed_reports=_shift(bench.honest.claimed_reports, -0.5), replay=None)
    assert not result.accepted and not result.checks["claimed_metrics_match"]["ok"]
    assert bench.cause(result) is FailureCause.METRIC_MISMATCH


def test_declared_evaluation_documents_are_contamination(bench: Bench) -> None:
    test_id = bench.world.dataset.splits[Split.TEST][0].candidate_id
    val_id = bench.world.dataset.splits[Split.VALIDATION][0].candidate_id
    for leaked in (test_id, val_id):
        result = bench.verdict(
            training_candidate_ids=[*bench.honest.training_candidate_ids, leaked], replay=None
        )
        assert not result.accepted and bench.cause(result) is FailureCause.CONTAMINATION
    for bogus in ([], ["no-such-document"]):
        result = bench.verdict(training_candidate_ids=bogus, replay=None)
        assert not result.accepted and bench.cause(result) is FailureCause.CONTAMINATION


def test_under_declared_training_data_is_detected(bench: Bench) -> None:
    result = bench.verdict(training_candidate_ids=bench.honest.training_candidate_ids[1:], replay=None)
    assert not result.accepted and not result.checks["declared_data_matches_recipe"]["ok"]
    assert bench.cause(result) is FailureCause.UNDECLARED_TRAINING


def test_candidate_secretly_trained_on_the_benchmark_is_unmasked(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exécuteur tricheur : entraîne sur les documents HELD-OUT (donc un
    candidat artificiellement avantagé par le benchmark) mais déclare la
    recette honnête et ses documents train."""
    with monkeypatch.context() as m:
        m.setattr(
            World,
            "train_candidates",
            lambda self, d: tuple(
                c for c in self.dataset.splits[Split.TEST] if self.domain_of(c.source_id) == d
            ),
        )
        cheat = execute(bench.plan, bench.world, bench.store, bench.root / "cheat")
    assert cheat.artifact != bench.honest.artifact
    common = {
        "candidate_artifact": cheat.artifact,
        "claimed_reports": cheat.claimed_reports,  # mesures sincères du tricheur
        "training_candidate_ids": bench.honest.training_candidate_ids,  # déclaration mensongère
    }
    without_replay = bench.verdict(replay=None, **common)
    # Sans rejeu, rien dans les déclarations ne trahit la fraude : c'est le rejeu qui la démasque.
    assert all(c["ok"] for c in without_replay.checks.values())
    result = bench.verdict(replay="replay-cheat", **common)
    assert not result.accepted and not result.checks["training_replayed"]["ok"]
    assert bench.cause(result) is FailureCause.UNDECLARED_TRAINING
    assert result.decision == "reject"


def test_candidate_identical_to_baseline_is_not_an_improvement(bench: Bench) -> None:
    plan = _plan(bench.world, replace(RECIPE, init="champion", lr=5e-3))
    plan = replace(plan, baseline={"name": "c", "artifact": bench.honest.artifact}, baseline_recipe=RECIPE)
    result = bench.judge.judge(
        candidate_artifact=bench.honest.artifact,
        claimed_reports=bench.honest.claimed_reports,
        training_candidate_ids=bench.honest.training_candidate_ids,
        baseline_artifact=bench.honest.artifact,
        baseline_seed=0,
    )
    assert not result.accepted and not result.checks["candidate_differs"]["ok"]
    assert analyze_judgement(plan, result).cause is FailureCause.NO_CHANGE  # type: ignore[union-attr]


def test_baseline_modified_after_the_fact_is_detected(bench: Bench) -> None:
    other = execute(
        _plan(bench.world, replace(RECIPE, seed=1)), bench.world, bench.store, bench.root / "s1"
    )
    result = bench.judge.judge(
        candidate_artifact=other.artifact,
        claimed_reports=other.claimed_reports,
        training_candidate_ids=other.training_candidate_ids,
        baseline_artifact=bench.honest.artifact,
        baseline_seed=0,
        baseline_stored_reports=_shift(bench.honest.claimed_reports, +0.3),  # registre embelli a posteriori
    )
    assert not result.accepted and not result.checks["baseline_unchanged"]["ok"]
    assert bench.cause(result) is FailureCause.BASELINE_CHANGED


def test_corrupt_candidate_checkpoint_is_rejected(bench: Bench, tmp_path: Path) -> None:
    store = LocalFilesystemArtifactStore(root=str(tmp_path / "a"))
    meta = store.put(bench.store.get(bench.honest.artifact))
    Path(store._content_path(content_hash_hex(meta.content_hash))).write_bytes(b"not a checkpoint")
    result = IndependentJudge(bench.world, store).judge(
        candidate_artifact=meta.content_hash,
        claimed_reports=bench.honest.claimed_reports,
        training_candidate_ids=bench.honest.training_candidate_ids,
        baseline_artifact=None,
        baseline_seed=0,
    )
    assert not result.accepted and bench.cause(result) is FailureCause.CORRUPT_CHECKPOINT


def test_replay_timeout_is_a_timeout_not_a_fraud(bench: Bench) -> None:
    plan = replace(bench.plan, budget=Budget(200, 20_000, 1e-9))
    result = bench.judge.judge(
        candidate_artifact=bench.honest.artifact,
        claimed_reports=bench.honest.claimed_reports,
        training_candidate_ids=bench.honest.training_candidate_ids,
        baseline_artifact=None,
        baseline_seed=0,
        plan=plan,
        replay_dir=bench.root / "replay-timeout",
    )
    assert not result.accepted
    assert analyze_judgement(plan, result).cause is FailureCause.TIMEOUT  # type: ignore[union-attr]


def test_stop_during_replay_propagates(bench: Bench) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(StopRequested):
        bench.verdict(replay="replay-stop", stop=stop)


# Registre, journal, contrôleur --------------------------------------------------------

LIMITS = LoopLimits(
    max_total_experiments=1,
    max_steps_per_experiment=120,
    max_tokens_per_experiment=30_000,
    checkpoint_every=10,
    experiment_timeout_s=300,
)


def _controller(workdir: Path, **over: object) -> LoopController:
    kwargs: dict[str, object] = {
        "limits": LIMITS,
        "policy": StrategyPolicy(max_steps=120, max_tokens=30_000),
        "bootstrap": RECIPE,
        "log": lambda _m: None,
    }
    kwargs.update(over)
    return LoopController(workdir, support.world(), **kwargs)  # type: ignore[arg-type]


def test_champion_promoted_without_the_judge_is_withdrawn(bench: Bench, tmp_path: Path) -> None:
    """Promotion forgée : quelqu'un écrit directement au registre une version
    « accept » que la boucle n'a jamais jugée."""
    ctrl = _controller(tmp_path)
    meta = ctrl.store.put(bench.store.get(bench.honest.artifact))
    forged = ctrl.registry.propose(
        Proposal(
            "forged",
            bench.honest.checkpoint_sha256,
            bench.world.dataset.fingerprint,
            bench.honest.claimed_reports,
            "accept",
            artifact=meta.content_hash,
            metadata={"experiment_id": "exp-never-ran"},
        )
    )
    assert forged["promoted"]
    assert ctrl.attest(forged) is not None
    summary = ctrl.run()
    assert "champion rejeté" in summary["reason"] and summary["experiments"] == 0
    failed = ctrl.journal.loop_events("rollback_failed")
    assert failed and "aucune expérience du journal" in failed[-1]["detail"]


def test_duplicate_plan_is_refused_by_the_controller(tmp_path: Path) -> None:
    ctrl = _controller(tmp_path)
    world = support.world()
    plan = _plan(world)
    ctrl.journal.experiment(plan.experiment_id, "planned", plan=plan.to_dict(), decision={})
    ctrl.journal.experiment(plan.experiment_id, "aborted", reason="test")
    twin = _plan(world)  # autre identifiant, même empreinte
    decision = StrategyDecision("bootstrap", twin.hypothesis, twin.recipe, None, None, [])
    with pytest.raises(DuplicateExperimentError):
        ctrl._run(twin, decision)
    # Même une « nouvelle tentative » est refusée si la cause n'est pas réessayable.
    with pytest.raises(DuplicateExperimentError):
        ctrl._run(twin, replace(decision, retry_of=plan.experiment_id))


def test_experiment_planned_but_never_started_is_recovered(tmp_path: Path) -> None:
    ctrl = _controller(tmp_path)
    plan = _plan(support.world(), budget=ctrl._budget())
    ctrl.journal.loop("started", world_fingerprint=ctrl.world.fingerprint)
    ctrl.journal.experiment(plan.experiment_id, "planned", plan=plan.to_dict(), decision={})
    summary = _controller(tmp_path).run()
    assert summary["unfinished"] == [] and summary["experiments"] == 1
    record = ctrl.journal.experiments()[plan.experiment_id]
    events = [e["event"] for e in record.events]
    assert events[:3] == ["planned", "recovered", "started"] and record.finished
    assert record.payload("judged") is not None  # recovered and judged, not skipped


def test_stop_file_before_start_runs_nothing(tmp_path: Path) -> None:
    ctrl = _controller(tmp_path)
    ctrl.stop_file.write_text("stop")
    summary = ctrl.run()
    assert summary["experiments"] == 0 and "arrêt demandé" in summary["reason"]


def test_timeouts_are_bounded_and_never_promoted(tmp_path: Path) -> None:
    limits = replace(
        LIMITS, max_total_experiments=6, max_cycles=2, experiment_timeout_s=1e-9, max_retries=1
    )
    ctrl = _controller(tmp_path, limits=limits)
    summary = ctrl.run()
    records = list(ctrl.journal.experiments().values())
    assert summary["promoted"] == [] and records and all(r.finished for r in records)
    assert all((r.analysis or {}).get("cause") == "timeout" for r in records)
    by_plan: dict[str, int] = {}
    for r in records:
        by_plan[r.plan["fingerprint"]] = by_plan.get(r.plan["fingerprint"], 0) + 1
    assert max(by_plan.values()) <= 1 + limits.max_retries  # nouvelles tentatives bornées
    assert len(records) <= limits.max_total_experiments


def test_resume_after_stop_is_bit_identical(bench: Bench) -> None:
    stop = threading.Event()
    calls = {"n": 0}

    def on_step(step: int) -> None:
        calls["n"] += 1
        if step == 10:
            stop.set()

    run_dir = bench.root / "stopped"
    with pytest.raises(StopRequested):
        execute(bench.plan, bench.world, bench.store, run_dir, stop=stop, on_step=on_step)
    resumed = execute(bench.plan, bench.world, bench.store, run_dir)
    assert resumed.resumed_from_step == 10 and calls["n"] == 10
    assert resumed.artifact == bench.honest.artifact  # même contenu -> mêmes octets


def test_rejected_verdict_never_reaches_the_registry(bench: Bench, tmp_path: Path) -> None:
    ctrl = _controller(tmp_path)
    plan = bench.plan
    ctrl.journal.experiment(plan.experiment_id, "planned", plan=plan.to_dict(), decision={})
    ctrl.journal.experiment(
        plan.experiment_id,
        "executed",
        ok=True,
        artifact=bench.honest.artifact,
        checkpoint_sha256=bench.honest.checkpoint_sha256,
    )
    record = ctrl.journal.experiments()[plan.experiment_id]
    for decision in ("reject", "inconclusive"):
        verdict = JudgeResult(decision, ["test"], {}, candidate_reports=bench.honest.claimed_reports)
        ctrl._promote(plan, record=record, judged=verdict)
    assert ctrl.registry.entries() == []
    promotions = [
        e for e in ctrl.journal.experiments()[plan.experiment_id].events if e["event"] == "promotion"
    ]
    assert promotions and not any(e["promoted"] for e in promotions)
