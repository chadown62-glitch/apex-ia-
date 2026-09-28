"""APEX Live : l'état exposé à l'interface provient des fichiers écrits par le moteur (journal,
registre, télémétrie, verrou de boucle), jamais d'un état simulé. Boucle RÉELLE (monde réduit)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.live import BrainWorkdir
from apex.neural.loop import telemetry


@pytest.fixture(scope="module")
def loop_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from apex.neural.loop.controller import LoopController, LoopLimits
    from apex.neural.loop.strategy import StrategyPolicy
    from tests import loop_support as support

    workdir = tmp_path_factory.mktemp("live")
    support.world().save(workdir / "world.json")
    ctrl = LoopController(
        workdir,
        support.world(),
        limits=LoopLimits(
            max_cycles=2,
            max_experiments_per_cycle=1,
            max_total_experiments=2,
            max_steps_per_experiment=120,
            max_tokens_per_experiment=30_000,
            checkpoint_every=40,
            experiment_timeout_s=300,
        ),
        policy=StrategyPolicy(finetune_steps=60, finetune_tokens=12_000, max_steps=120),
        bootstrap=support.bootstrap_recipe(steps=80),
        log=lambda _m: None,
    )
    ctrl.run()
    return workdir


def _journal(workdir: Path) -> list[dict]:
    return [json.loads(line) for line in (workdir / "journal.jsonl").read_text().splitlines()]


def test_state_is_read_from_the_engine_files(loop_dir: Path) -> None:
    from apex.neural.registry import ModelRegistry

    state = BrainWorkdir(loop_dir).state()
    current = ModelRegistry(loop_dir / "registry.jsonl").current()
    assert current is not None
    champion = state["champion"]
    assert champion["name"] == current["name"] and champion["artifact"] == current["artifact"]
    assert champion["attested"] and champion["attestation_problem"] is None
    assert champion["reports"] == {n: round(r["value"], 5) for n, r in sorted(current["reports"].items())}
    raw = _journal(loop_dir)
    experiments = {e["experiment_id"] for e in raw if e["kind"] == "experiment"}
    assert {e["experiment_id"] for e in state["experiments"]} == experiments
    assert state["loop"]["state"] == "idle" and state["current"] is None
    assert state["world"]["status"] == "real"
    assert [p["group"] for p in state["protections"]] == ["text"]


def test_events_follow_the_journal_in_order(loop_dir: Path) -> None:
    brain = BrainWorkdir(loop_dir)
    events = brain.events()
    assert [e["seq"] for e in events] == [e["seq"] for e in _journal(loop_dir)]
    labels = [e["label"] for e in events]
    for expected in ("STRATEGY_SELECTED", "EXPERIMENT_STARTED", "TRAINING_COMPLETED", "LOOP_STOPPED"):
        assert expected in labels
    assert any(label.startswith("JUDGE_") for label in labels)
    assert "NEW_CHAMPION" in labels  # l'amorçage est promu dans ce monde réduit
    middle = events[len(events) // 2]["seq"]
    assert [e["seq"] for e in brain.events(after=middle)] == [e["seq"] for e in events if e["seq"] > middle]


def test_telemetry_reports_the_real_training_curve(loop_dir: Path) -> None:
    """La progression publiée pendant l'entraînement est celle que le moteur consigne à la fin
    (courbe de perte du journal) : même pas, même perte."""
    executed = [e for e in _journal(loop_dir) if e["event"] == "executed" and e.get("ok")]
    assert executed
    for event in executed:
        rows = telemetry.read(loop_dir / "runs" / event["experiment_id"])
        phases = [r["phase"] for r in rows]
        assert phases[:2] == [telemetry.TRAINING_STARTED, telemetry.DATASET_READY]
        assert telemetry.TRAINING_COMPLETED in phases and telemetry.JUDGE_STARTED in phases
        progress = [
            {k: r[k] for k in ("step", "loss", "lr")}
            for r in rows
            if r["phase"] == telemetry.TRAINING_PROGRESS
        ]
        assert progress[: len(event["curve"])] == event["curve"]
    # Une promotion exige une réplique : son entraînement publie aussi sa progression, marquée.
    promoted = {
        e["experiment_id"] for e in _journal(loop_dir) if e["event"] == "promotion" and e["promoted"]
    }
    for exp in promoted:
        rows = telemetry.read(loop_dir / "runs" / exp)
        assert any(r["phase"] == telemetry.REPLICATION_STARTED for r in rows)
        assert any(r["phase"] == telemetry.TRAINING_PROGRESS and r.get("run") == "replica" for r in rows)


def test_a_champion_whose_promotion_is_missing_from_the_journal_is_not_attested(
    loop_dir: Path, tmp_path: Path
) -> None:
    """Journal tronqué juste avant la promotion (chaîne encore valide) : le registre annonce un
    champion que le journal ne justifie plus. L'état le signale, sans le masquer."""
    copy = tmp_path / "copy"
    shutil.copytree(loop_dir, copy)
    lines = (copy / "journal.jsonl").read_text().splitlines()
    first_promotion = next(i for i, line in enumerate(lines) if json.loads(line)["event"] == "promotion")
    (copy / "journal.jsonl").write_text("\n".join(lines[:first_promotion]) + "\n")
    champion = BrainWorkdir(copy).state()["champion"]
    assert not champion["attested"] and "consigné" in champion["attestation_problem"]


def test_missing_sources_are_reported_as_not_available(tmp_path: Path) -> None:
    assert BrainWorkdir(tmp_path / "absent").state()["status"] == "not_available"
    empty = tmp_path / "empty"
    empty.mkdir()
    state = BrainWorkdir(empty).state()
    assert state["world"]["status"] == "not_available"
    assert state["champion"]["status"] == "not_available"
    assert state["protections"]["status"] == "not_available"
    assert state["experiments"] == [] and BrainWorkdir(empty).events() == []


def test_api_serves_the_engine_state_and_never_masks_a_broken_chain(
    loop_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api_test_support import authed_client

    client = authed_client()
    monkeypatch.delenv("APEX_BRAIN_WORKDIR", raising=False)
    unconfigured = client.get("/api/v1/brain").json()
    assert unconfigured["status"] == "unknown" and "APEX_BRAIN_WORKDIR" in unconfigured["detail"]

    monkeypatch.setenv("APEX_BRAIN_WORKDIR", str(loop_dir))
    body = client.get("/api/v1/brain").json()
    assert body["status"] == "real" and body["data"]["champion"]["attested"]
    events = client.get("/api/v1/brain/events", params={"after": 2}).json()
    assert events["status"] == "real" and all(e["seq"] > 2 for e in events["data"])
    # Sans curseur : tout le journal, dont le premier événement (numéro 0).
    first = client.get("/api/v1/brain/events").json()["data"]
    assert [e["seq"] for e in first] == list(range(len(_journal(loop_dir))))

    tampered = tmp_path / "tampered"
    shutil.copytree(loop_dir, tampered)
    text = (tampered / "journal.jsonl").read_text()
    (tampered / "journal.jsonl").write_text(
        text.replace('"decision": "reject"', '"decision": "accept"', 1)
        if '"decision": "reject"' in text
        else text.replace('"ok": true', '"ok": false', 1)
    )
    monkeypatch.setenv("APEX_BRAIN_WORKDIR", str(tampered))
    for path in ("/api/v1/brain", "/api/v1/brain/events"):
        broken = client.get(path).json()
        assert broken["status"] == "unknown" and "intégrité" in broken["detail"]


def test_the_loop_is_running_only_while_its_lock_is_held(loop_dir: Path, tmp_path: Path) -> None:
    """Verrou `flock` RÉEL (comme `LoopController`) : le pid laissé dans `loop.lock` après l'arrêt ne
    compte pas, un verrou pris sur un AUTRE fichier non plus ; seul le détenteur du verrou compte."""
    import fcntl
    import os

    copy = tmp_path / "copy"
    shutil.copytree(loop_dir, copy)
    (copy / "loop.lock").write_text(str(os.getpid()))  # pid vivant, verrou libre
    with (tmp_path / "unrelated.lock").open("w") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert BrainWorkdir(copy).state()["loop"]["state"] == "idle"
        with (copy / "loop.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            loop = BrainWorkdir(copy).state()["loop"]
            assert loop["state"] == "running" and loop["pid"] == os.getpid()
            assert loop["resources"]["status"] == "real" and loop["resources"]["rss_mb"] > 0
    assert BrainWorkdir(copy).state()["loop"]["state"] == "idle"


def test_experiment_summaries_carry_the_judge_measures(loop_dir: Path) -> None:
    judged = {e["experiment_id"]: e["result"] for e in _journal(loop_dir) if e["event"] == "judged"}
    for summary in BrainWorkdir(loop_dir).state()["experiments"]:
        result = judged[summary["experiment_id"]]
        assert summary["decision"] == result["decision"] or summary["decision"] == "reject"
        assert summary["forgotten"] == [
            f["suite"] for f in result.get("forgetting", []) if f["status"] == "forgotten"
        ]
        assert summary["learning"] == {c: v["kinds"] for c, v in result.get("learning", {}).items()}
        target = next(c for c in result["comparisons"] if c["suite"] == summary["target"])
        assert (summary["target_before"], summary["target_after"]) == (
            target["baseline_bpb"],
            target["candidate_bpb"],
        )
