"""La capacité CODE REPAIR devient une CIBLE de la boucle autonome de
Priority 1 : observation de la faiblesse (exécution sandbox), hypothèse
pré-enregistrée sur la suite held-out de la capacité, Judge qui décide sur
cette suite, mémoire des tentatives par tâche dans le journal. Monde réduit
réel, entraînements réels."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.capabilities.code_repair.benchmark import suite_name
from apex.capabilities.registry import sandbox_provider
from apex.neural.loop.controller import LoopController, LoopLimits
from apex.neural.loop.plan import InvalidPlanError
from apex.neural.loop.strategy import DIRECTED, StrategyPolicy
from apex.neural.loop.world import PRIMARY
from tests import capability_support as support

try:
    sandbox_provider()
    SANDBOX = True
except Exception:  # pragma: no cover
    SANDBOX = False
pytestmark = pytest.mark.skipif(not SANDBOX, reason="aucun sandbox NAMESPACED sur cet hôte")

TARGET = suite_name("heldout")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> LoopController:
    ctrl = LoopController(
        tmp_path_factory.mktemp("caploop"),
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
        policy=StrategyPolicy(finetune_steps=60, finetune_lr=5e-3, finetune_tokens=12_000, max_steps=120),
        bootstrap=support.recipe(steps=120, mix={"code": 1.0}),
        log=lambda _m: None,
    )
    ctrl.run()
    return ctrl


def test_capability_weakness_becomes_the_target_of_the_loop(run: LoopController) -> None:
    records = list(run.journal.experiments().values())
    assert len(records) == 2 and all(r.finished for r in records)
    bootstrap, targeted = records
    assert (bootstrap.payload("judged") or {})["result"]["primary"] == PRIMARY
    assert bootstrap.promoted, "l'amorçage doit donner un champion pour que la capacité devienne la cible"
    plan = targeted.plan
    assert plan["target_benchmark"] == TARGET
    assert plan["hypothesis"]["weakness"]["domain"] == "code"
    assert "repair_cost" in plan["hypothesis"]["claim"] and TARGET in plan["hypothesis"]["falsification"]
    result = (targeted.payload("judged") or {})["result"]
    assert result["primary"] == TARGET  # la décision porte sur la suite held-out de la capacité
    comparisons = {c["suite"]: c for c in result["comparisons"]}
    assert comparisons[TARGET]["metric"] == "repair_cost"
    # mémorisation / généralisation / transfert : calculés par le Judge sur ses propres mesures
    learning = result["learning"]["code_repair"]
    assert learning["status"] == "ESTABLISHED" and learning["kinds"]
    bootstrap_learning = (bootstrap.payload("judged") or {})["result"]["learning"]["code_repair"]
    assert bootstrap_learning["saw_task_solutions"] is False  # mélange « code » seul
    assert "MEMORIZATION" not in bootstrap_learning["kinds"]
    held_gain = comparisons[TARGET]["bpb_gain_ci"]
    assert ("GENERALIZATION" in learning["kinds"]) == (
        comparisons[TARGET]["verdict"] == "significant" and held_gain[0] > 0
    )


def test_failures_are_remembered_task_by_task(run: LoopController) -> None:
    for record in run.journal.experiments().values():
        judged = record.payload("judged") or {}
        details = judged["capability_details"]
        assert set(details) == set(support.benchmark().suites)
        for task in details[TARGET]:
            assert task["attempts"] and all(a["executed"] for a in task["attempts"])
            if not task["solved"]:
                assert all(a["first_failure"] for a in task["attempts"])  # cause d'échec mesurée
        changes = judged["result"]["task_changes"][TARGET]
        assert changes["tasks"] == len(details[TARGET])
    targeted = list(run.journal.experiments().values())[-1]
    if not targeted.promoted:
        assert (targeted.analysis or {}).get("status") in ("established", "unknown")


def test_registry_records_the_preregistered_primary(run: LoopController) -> None:
    for entry in run.registry.entries():
        exp = entry["metadata"]["experiment_id"]
        plan = run.journal.experiments()[exp].plan
        assert entry["primary"] == (TARGET if plan["target_benchmark"] == TARGET else PRIMARY)


def test_workdir_state_is_consistent(run: LoopController, tmp_path: Path) -> None:
    summary = run.summary()
    assert summary["unfinished"] == [] and summary["experiments"] == 2
    assert run.attest(run.registry.current()) is None  # type: ignore[arg-type]


# Expérience dirigée : la question vient de l'opérateur, la réponse de la boucle ---------------


def test_directed_experiment_is_refused_without_a_measured_weakness_or_with_two_changes(
    run: LoopController,
) -> None:
    before = len(run.journal.entries())
    with pytest.raises(ValueError, match="aucune faiblesse mesurée"):
        run.run_directed({"steps": 90}, target="cap:code_repair:nope", reason="cible inventée")
    with pytest.raises(InvalidPlanError):
        run.run_directed({"steps": 90, "lr": 1e-3}, target=TARGET, reason="deux modifications")
    assert len(run.journal.entries()) == before  # rien n'est consigné pour un plan refusé


def test_directed_experiment_respects_the_loop_ceilings(run: LoopController) -> None:
    before = len(run.journal.entries())
    with pytest.raises(RuntimeError, match="plafond de 2 expériences"):
        run.run_directed({"mix": {"code_tasks": 1.0}}, target=TARGET, reason="au-delà du plafond")
    assert len(run.journal.entries()) == before


def test_directed_experiment_goes_through_the_independent_judge(run: LoopController) -> None:
    # Comme la CLI `directed` : un nouveau contrôleur sur le même répertoire, avec ses propres plafonds.
    ctrl = LoopController(
        run.workdir,
        run.world,
        limits=replace(run.limits, max_total_experiments=3),
        policy=run.policy,
        log=lambda _m: None,
    )
    # La question : entraîner sur les SOLUTIONS des tâches train généralise-t-il au held-out ?
    record = ctrl.run_directed(
        {"mix": {"code_tasks": 1.0}}, target=TARGET, reason="mémorisation ou généralisation ?"
    )
    assert record.finished and record.decision in ("accept", "reject")
    assert record.plan["hypothesis"]["strategy"] == DIRECTED
    assert any(e["experiment"] == record.experiment_id for e in ctrl.journal.loop_events("directed"))
    result = (record.payload("judged") or {})["result"]
    assert result["primary"] == TARGET and result["judge_id"]
    learning = result["learning"]["code_repair"]
    assert learning["saw_task_solutions"] is True  # établi sur les documents rejoués, pas la recette
    comparisons = {c["suite"]: c for c in result["comparisons"]}
    train = comparisons[suite_name("train")]
    if "MEMORIZATION" in learning["kinds"]:
        assert train["verdict"] == "significant" and train["bpb_gain_ci"][0] > 0
        assert "GENERALIZATION" not in learning["kinds"]
    if record.decision == "reject":
        assert (record.analysis or {}).get("cause")  # l'échec est analysé comme les autres
    assert ctrl.attest(ctrl.registry.current()) is None  # type: ignore[arg-type]
