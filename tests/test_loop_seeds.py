"""Protocole MULTI-GRAINES (P3.7) sur une boucle RÉELLE réduite : chaque expérience depuis un
champion est entraînée sur au moins deux graines, jugées chacune par le Judge ; le verdict est
consigné, recalculable depuis le journal, et le registre le recalcule lui-même avant promotion."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.evaluation import EvalReport
from apex.neural.loop.controller import LoopController, LoopLimits
from apex.neural.loop.plan import ExperimentPlan
from apex.neural.loop.strategy import StrategyPolicy
from apex.neural.registry import ModelRegistry, Proposal
from apex.neural.variance import pooled_sigma, seed_verdict
from tests import loop_support as support


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> LoopController:
    workdir = tmp_path_factory.mktemp("seeds")
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
    return ctrl


def _finetune(ctrl: LoopController):  # type: ignore[no-untyped-def]
    records = [r for r in ctrl.journal.experiments().values() if r.plan.get("baseline", {}).get("artifact")]
    assert records, "aucune expérience depuis un champion"
    return records[-1]


def test_an_experiment_from_a_champion_is_decided_on_two_independent_seeds(seeded: LoopController) -> None:
    record = _finetune(seeded)
    events = [e["event"] for e in record.events]
    assert events.index("judged") < events.index("replicated") < events.index("seed_verdict")
    assert events[-2:] == ["analyzed", "promotion"]
    replica = record.replications[0]
    assert replica["seed_index"] == 1 and replica["seed"] != record.plan["recipe"]["seed"]
    assert replica["candidate_reports"] and replica["invalid"] == []
    verdict = record.payload("seed_verdict")["result"]  # type: ignore[index]
    assert verdict["k"] == len(record.replications) + 1 >= 2
    assert record.decision == verdict["decision"]
    assert isinstance(record.payload("planned")["variance_prior"], dict)  # type: ignore[index]


def test_the_journaled_verdict_is_recomputable_from_the_journal_alone(seeded: LoopController) -> None:
    record = _finetune(seeded)
    seeds, invalid = seeded._seed_reports(record)
    settings = seeded._variance_settings(ExperimentPlan.from_dict(record.plan))
    stored = record.payload("seed_verdict")["result"]  # type: ignore[index]
    recomputed = seed_verdict(
        seeds=seeds,
        protections=seeded._protections(),
        tolerances=settings["tolerances"],
        prior=record.payload("planned")["variance_prior"],  # type: ignore[index]
        target=settings["target"],
        memorization_suite=settings["memorization_suite"],
        new_capability=settings["capability_target"],
        invalid=invalid,
        max_seeds=seeded.limits.max_seeds,
    )
    assert recomputed["decision"] == stored["decision"]
    assert [(r["suite"], r["status"], r["delta"]) for r in recomputed["rows"]] == [
        (r["suite"], r["status"], r["delta"]) for r in stored["rows"]
    ]


def test_promotion_goes_through_the_registry_recomputation(seeded: LoopController) -> None:
    record = _finetune(seeded)
    promotion = record.payload("promotion")
    assert promotion is not None
    if record.promoted:
        entry = seeded.registry.entries()[promotion["registry_index"]]
        assert entry["reasons"][0].startswith("verdict recalculé par le registre")
        assert entry["metadata"]["seeds"] >= 2
    else:
        assert promotion["reason"].startswith("verdict sur")


def test_the_margin_prior_comes_only_from_the_imported_history(
    seeded: LoopController, tmp_path: Path
) -> None:
    """Les expériences de ce journal n'alimentent pas la marge ; l'historique importé, si."""
    import shutil

    assert seeded._variance_prior() == ({}, [])  # rien d'importé : aucune marge tirée du journal
    copy = tmp_path / "copy"
    shutil.copytree(seeded.workdir, copy)
    (copy / "loop.lock").unlink(missing_ok=True)
    ctrl = LoopController(copy, seeded.world, log=lambda _m: None)
    imported = ctrl.import_replications([seeded.workdir])
    assert imported["groups"] >= 1
    groups = [g["values"] for e in ctrl.journal.loop_events("replication_history") for g in e["groups"]]
    prior, sources = ctrl._variance_prior()
    assert prior == pooled_sigma(groups) and prior
    assert all("experiment_id" not in s for s in sources)


def test_a_destructive_noisy_experiment_does_not_widen_the_margin(
    seeded: LoopController, tmp_path: Path
) -> None:
    """Adversarial (vulnérabilité démontrée sur la boucle L) : une expérience destructrice dont les
    graines divergent énormément, consignée dans le journal, ne doit pas élargir la marge des
    expériences suivantes."""
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(seeded.workdir, copy)
    (copy / "loop.lock").unlink(missing_ok=True)
    ctrl = LoopController(copy, seeded.world, log=lambda _m: None)
    ctrl.import_replications([seeded.workdir])
    before = ctrl._variance_prior()
    plan = _finetune(seeded).plan
    suites = list(before[0]) or [next(iter(seeded.world.benchmarks))]
    ctrl.journal.experiment("destructive", "planned", plan=plan)
    ctrl.journal.experiment(
        "destructive",
        "seed_verdict",
        result={"decision": "reject", "rows": [{"suite": s, "seeds": [0.0, 9.0, 4.0]} for s in suites]},
    )
    assert ctrl._variance_prior() == before


def test_the_registry_refuses_a_reproduced_regression_whatever_the_caller_claims(
    seeded: LoopController, tmp_path: Path
) -> None:
    """Le contrôleur annonce ACCEPT, mais les mesures des graines montrent une régression reproduite
    sur une garde : le registre, qui RECALCULE, refuse."""
    champion = seeded.registry.current()
    assert champion is not None
    reports = {n: EvalReport.from_dict(r) for n, r in champion["reports"].items()}

    def degraded(delta: float) -> dict[str, EvalReport]:
        out = dict(reports)
        guard = next(n for n in reports if n.startswith("heldout:"))
        report = reports[guard]
        out[guard] = EvalReport(
            report.benchmark,
            report.benchmark_fingerprint,
            "forged",
            tuple(
                type(s)(s.doc_id, s.bits_per_byte + delta, s.token_accuracy, s.n_bytes, s.n_tokens)
                for s in report.scores
            ),
            report.metric,
        )
        return out

    registry = ModelRegistry(
        tmp_path / "registry.jsonl",
        acceptance=seeded.world.acceptance_suites,
        tolerances=seeded.world.metric_tolerances,
        protected_groups=seeded.world.protected_groups,
    )
    registry.adopt(
        name="champion",
        checkpoint_sha256="x",
        dataset_fingerprint="d",
        reports=reports,
        artifact="sha256:a",
        metadata={},
    )
    entry = registry.propose(
        Proposal(
            name="claimed-accept",
            checkpoint_sha256="y",
            dataset_fingerprint="d",
            reports=degraded(0.5),
            in_run_decision="accept",
            artifact="sha256:b",
            seed_reports=(degraded(0.6),),
            variance={"prior": {}, "memorization_suite": None, "capability_target": False, "max_seeds": 3},
        )
    )
    assert not entry["promoted"] and any("régression reproduite" in r for r in entry["reasons"])


def test_an_invalid_replica_invalidates_the_whole_experiment(
    seeded: LoopController, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injection de faute : le rejeu de la graine 1 échoue (provenance non prouvée). Aucune graine
    invalide n'est « compensée » par une autre : l'expérience est INVALIDE, jamais promue."""
    import shutil
    from dataclasses import replace

    from apex.neural.loop.judge import IndependentJudge

    copy = tmp_path / "copy"
    shutil.copytree(seeded.workdir, copy)
    (copy / "loop.lock").unlink(missing_ok=True)
    ctrl = LoopController(
        copy,
        seeded.world,
        limits=replace(seeded.limits, max_total_experiments=5, max_total_steps=10_000),
        policy=seeded.policy,
        log=lambda _m: None,
    )
    original = IndependentJudge.judge

    def judge(self: IndependentJudge, **kwargs: object):  # type: ignore[no-untyped-def]
        result = original(self, **kwargs)  # type: ignore[arg-type]
        plan = kwargs.get("plan")
        if plan is not None and plan.experiment_id.endswith("-replica"):  # type: ignore[attr-defined]
            return replace(
                result, checks={**result.checks, "training_replayed": {"ok": False, "detail": "injecté"}}
            )
        return result

    monkeypatch.setattr(IndependentJudge, "judge", judge)
    target = ctrl._observe_with_integrity().weaknesses[0].benchmark
    record = ctrl.run_directed({"steps": 90}, target=target, reason="graine 1 invalide (injection)")
    verdict = record.payload("seed_verdict")["result"]  # type: ignore[index]
    assert verdict["decision"] == "invalid" and "training_replayed#1" in verdict["reasons"]
    assert record.replications[0]["invalid"] == ["training_replayed"]
    assert not record.promoted and record.analysis["cause"] == "undeclared_training"  # type: ignore[index]


def test_verify_checks_the_whole_directory_after_a_copy(seeded: LoopController, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(seeded.workdir, copy)
    (copy / "loop.lock").unlink(missing_ok=True)
    clean = LoopController(copy, seeded.world, log=lambda _m: None).verify()
    assert clean["ok"] and clean["artifacts_checked"] >= 3 and clean["unfinished"] == []
    champion = seeded.registry.current()
    assert champion is not None
    digest = champion["artifact"].split(":")[1]
    blob = next(p for p in (copy / "artifacts").rglob(digest) if p.is_file())  # le contenu, pas .meta.json
    data = bytearray(blob.read_bytes())
    data[len(data) // 2] ^= 0xFF  # un octet inversé au milieu (la fin d'un zip vaut souvent déjà 0)
    blob.write_bytes(bytes(data))
    corrupted = LoopController(copy, seeded.world, log=lambda _m: None).verify()
    assert not corrupted["ok"] and any("altéré" in p for p in corrupted["problems"])
    blob.unlink()
    missing = LoopController(copy, seeded.world, log=lambda _m: None).verify()
    assert missing["artifacts_missing"] >= 1 and any("absent" in p for p in missing["problems"])
