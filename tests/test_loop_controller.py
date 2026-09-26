"""Boucle autonome de bout en bout sur un monde réel réduit : invariants,
bornes, reproductibilité, arrêt, crash et reprise, verrou, retour arrière."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.loop.controller import (
    LoopBusyError,
    LoopController,
    LoopLimits,
    WorldMismatchError,
)
from apex.neural.loop.journal import ExperimentJournal
from apex.neural.loop.strategy import StrategyPolicy
from apex.neural.training import TrainingEngine
from tests import loop_support as support

LIMITS = LoopLimits(
    max_cycles=2,
    max_experiments_per_cycle=2,
    max_total_experiments=4,
    max_steps_per_experiment=120,
    max_total_steps=1_000,
    max_tokens_per_experiment=30_000,
    checkpoint_every=20,
    experiment_timeout_s=300,
)
POLICY = StrategyPolicy(
    finetune_steps=40, finetune_lr=5e-3, finetune_tokens=10_000, max_steps=120, max_tokens=30_000
)


def controller(workdir: Path, **over: object) -> LoopController:
    kwargs: dict[str, object] = {
        "limits": LIMITS,
        "policy": POLICY,
        "bootstrap": support.bootstrap_recipe(),
        "log": lambda _m: None,
    }
    kwargs.update(over)
    return LoopController(workdir, support.world(), **kwargs)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[LoopController, dict[str, object]]:
    ctrl = controller(tmp_path_factory.mktemp("loop"))
    return ctrl, ctrl.run()


def test_loop_invariants_on_a_real_run(completed_run: tuple[LoopController, dict[str, object]]) -> None:
    ctrl, summary = completed_run
    records = ctrl.journal.experiments()
    assert 1 <= len(records) <= LIMITS.max_total_experiments
    assert summary["unfinished"] == []
    promoted_ids = {
        e["metadata"]["experiment_id"]
        for e in ctrl.registry.entries()
        if e["promoted"] and e.get("kind") == "proposal"
    }
    for record in records.values():
        assert record.finished
        assert record.payload("planned") and record.payload("started")
        if record.promoted:
            assert record.decision == "accept" and record.experiment_id in promoted_ids
        else:
            assert record.experiment_id not in promoted_ids  # jamais de promotion sans ACCEPT
            assert record.analysis is not None and record.analysis.get("cause")  # tout rejet est analysé
        plan = record.plan
        assert len(plan["modifications"]) == 1  # une seule modification par expérience
        if plan["parent_experiment_id"]:
            assert plan["parent_experiment_id"] in records
    fingerprints = [r.plan["fingerprint"] for r in records.values()]
    assert len(fingerprints) == len(set(fingerprints))  # jamais deux fois la même expérience
    for cycle in ctrl.journal.loop_events("cycle_completed"):
        selection = cycle["selection"]  # sélection du meilleur : seule une version promue est retenue
        promoted_here = [r["experiment_id"] for r in cycle["comparison"] if r["promoted"]]
        if promoted_here:
            assert selection["selected"] == promoted_here[-1]
        else:
            assert selection["selected"] != (selection["best_rejected"] or {}).get("experiment_id")
    assert int(summary["steps_used"]) <= LIMITS.max_total_steps  # type: ignore[call-overload]
    assert ctrl.journal.entries()  # chaîne vérifiée à la lecture


def test_loop_is_reproducible(
    completed_run: tuple[LoopController, dict[str, object]], tmp_path: Path
) -> None:
    ctrl, _ = completed_run
    again = controller(tmp_path)
    again.run()

    def trace(c: LoopController) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for r in c.journal.experiments().values():
            judged = r.payload("judged") or {}
            rows.append(
                (
                    r.plan["hypothesis"]["strategy"],
                    r.plan["fingerprint"],
                    r.decision,
                    r.promoted,
                    judged.get("result", {}).get("candidate", {}).get("heldout", {}).get("bits_per_byte"),
                )
            )
        return rows

    assert trace(again) == trace(ctrl)


def test_limits_stop_the_loop(tmp_path: Path) -> None:
    ctrl = controller(
        tmp_path,
        limits=LoopLimits(
            max_total_experiments=1,
            max_steps_per_experiment=120,
            max_tokens_per_experiment=30_000,
            max_total_steps=1_000,
        ),
    )
    summary = ctrl.run()
    assert summary["experiments"] == 1 and "plafond" in summary["reason"]
    with pytest.raises(ValueError, match="strictement positive"):
        LoopLimits(max_cycles=0)


def test_stop_request_then_resume_gives_the_same_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = controller(
        tmp_path / "ref",
        limits=LoopLimits(
            max_total_experiments=1,
            max_steps_per_experiment=120,
            max_tokens_per_experiment=30_000,
            checkpoint_every=20,
        ),
    )
    reference.run()
    ref_exec = next(iter(reference.journal.experiments().values())).payload("executed")

    stop = threading.Event()
    real_step = TrainingEngine.train_step

    def step_then_stop(self: TrainingEngine, batcher: object) -> object:
        result = real_step(self, batcher)  # type: ignore[arg-type]
        if self.step == 30:
            stop.set()
        return result

    monkeypatch.setattr(TrainingEngine, "train_step", step_then_stop)
    limits = LoopLimits(
        max_total_experiments=1,
        max_steps_per_experiment=120,
        max_tokens_per_experiment=30_000,
        checkpoint_every=20,
    )
    first = controller(tmp_path / "run", limits=limits, stop=stop)
    summary = first.run()
    assert "arrêt demandé" in summary["reason"] and len(summary["unfinished"]) == 1
    record = next(iter(first.journal.experiments().values()))
    assert record.status == "interrupted"
    monkeypatch.setattr(TrainingEngine, "train_step", real_step)
    resumed = controller(tmp_path / "run", limits=limits).run()
    assert resumed["unfinished"] == []
    record = next(iter(ExperimentJournal(tmp_path / "run" / "journal.jsonl").experiments().values()))
    events = [e["event"] for e in record.events]
    assert "interrupted" in events and "recovered" in events
    executed = record.payload("executed")
    assert executed is not None and ref_exec is not None
    assert executed["resumed_from_step"] == 30  # reprise au dernier checkpoint de reprise, pas depuis zéro
    assert executed["checkpoint_sha256"] != "" and executed["artifact"] == ref_exec["artifact"]  # bit à bit


class _Crash(BaseException):
    """Mort brutale du processus : aucune ligne de journal après ce point."""


def test_crash_mid_experiment_is_recovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_step = TrainingEngine.train_step

    def crash_at_45(self: TrainingEngine, batcher: object) -> object:
        if self.step == 45:
            raise _Crash
        return real_step(self, batcher)  # type: ignore[arg-type]

    limits = LoopLimits(
        max_total_experiments=1,
        max_steps_per_experiment=120,
        max_tokens_per_experiment=30_000,
        checkpoint_every=20,
    )
    monkeypatch.setattr(TrainingEngine, "train_step", crash_at_45)
    with pytest.raises(_Crash):
        controller(tmp_path, limits=limits).run()
    record = next(iter(ExperimentJournal(tmp_path / "journal.jsonl").experiments().values()))
    assert record.status == "started"  # le crash n'a rien écrit
    monkeypatch.setattr(TrainingEngine, "train_step", real_step)
    summary = controller(tmp_path, limits=limits).run()
    assert summary["unfinished"] == []
    record = next(iter(ExperimentJournal(tmp_path / "journal.jsonl").experiments().values()))
    executed = record.payload("executed")
    assert executed is not None and executed["resumed_from_step"] == 40
    assert record.finished


def test_single_controller_per_directory(tmp_path: Path) -> None:
    ctrl = controller(tmp_path)
    with ctrl._lock(), pytest.raises(LoopBusyError):
        controller(tmp_path).run()


def test_journal_from_another_world_is_refused(tmp_path: Path) -> None:
    ctrl = controller(tmp_path)
    ctrl.journal.loop("started", world_fingerprint="sha256:" + "9" * 64)
    with pytest.raises(WorldMismatchError):
        ctrl.run()


def test_corrupted_champion_triggers_rollback(
    completed_run: tuple[LoopController, dict[str, object]], tmp_path: Path
) -> None:
    import shutil

    from apex.artifacts.model import content_hash_hex
    from apex.neural.evaluation import DocScore, EvalReport
    from apex.neural.registry import Proposal

    source, _ = completed_run
    first = source.registry.current()
    assert first is not None, "l'exécution de référence doit avoir promu un champion"
    workdir = tmp_path / "rb"
    shutil.copytree(source.workdir, workdir, ignore=shutil.ignore_patterns("loop.lock"))
    ctrl = controller(workdir)
    # Une seconde version promue, dont on corrompt ensuite l'artefact sur disque.
    second = ctrl.store.put(ctrl.store.get(first["artifact"]) + b"\x00")
    better = {
        n: EvalReport(
            r["benchmark"],
            r["benchmark_fingerprint"],
            r["model_fingerprint"],
            tuple(DocScore(d, b - 0.5, a, nb, nt) for d, b, a, nb, nt in r["scores"]),
        )
        for n, r in first["reports"].items()
    }
    entry = ctrl.registry.propose(
        Proposal(
            "second", "x", first["dataset_fingerprint"], better, "accept", artifact=second.content_hash
        )
    )
    assert entry["promoted"] and ctrl.registry.current()["name"] == "second"  # type: ignore[index]
    Path(ctrl.store._content_path(content_hash_hex(second.content_hash))).write_bytes(b"corrupted")
    obs = ctrl._observe_with_integrity()
    assert obs.champion is not None and obs.champion.name == first["name"] and obs.champion.integrity_ok
    rollback = ctrl.journal.loop_events("rollback")[-1]
    assert rollback["from_champion"] == "second" and "ArtifactIntegrityError" in rollback["reason"]
    assert ctrl.registry.current()["kind"] == "rollback"  # type: ignore[index]
