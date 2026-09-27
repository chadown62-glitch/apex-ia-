"""F2b — une promotion exige une RÉPLIQUE : même plan, graine indépendante, entraînement réel et
jugement complet (rejeu compris). Mesuré en faisabilité P3 : deux entraînements d'une même
configuration peuvent différer de 0,725 à 0,90 ; un gain qui tient à la graine n'est pas appris.
Monde réduit réel (docstrings de la stdlib, documentation du dépôt), entraînements réels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.loop.controller import REPLICA_SEED_OFFSET, LoopController, LoopLimits
from apex.neural.loop.failure import FailureCause
from apex.neural.loop.strategy import MORE_STEPS, StrategyPolicy
from tests import loop_support as support

LIMITS = LoopLimits(
    max_cycles=1,
    max_experiments_per_cycle=1,
    max_total_experiments=1,
    max_steps_per_experiment=120,
    max_tokens_per_experiment=30_000,
    checkpoint_every=40,
    experiment_timeout_s=300,
)


def controller(workdir: Path, limits: LoopLimits = LIMITS) -> LoopController:
    return LoopController(
        workdir,
        support.world(),
        limits=limits,
        policy=StrategyPolicy(finetune_steps=60, finetune_tokens=12_000, max_steps=120),
        bootstrap=support.bootstrap_recipe(steps=80),
        log=lambda _m: None,
    )


@pytest.fixture(scope="module")
def replicated_run(tmp_path_factory: pytest.TempPathFactory) -> LoopController:
    ctrl = controller(tmp_path_factory.mktemp("replicated"))
    ctrl.run()
    return ctrl


def test_an_accepted_experiment_is_replicated_with_an_independent_seed(
    replicated_run: LoopController,
) -> None:
    record = next(iter(replicated_run.journal.experiments().values()))
    first = record.payload("judged") or {}
    replica = record.payload("replicated")
    assert first["result"]["decision"] == "accept", "l'amorçage doit être accepté pour exercer la réplique"
    assert replica is not None, "une acceptation sans réplique ne doit jamais promouvoir"
    assert replica["seed"] == record.plan["recipe"]["seed"] + REPLICA_SEED_OFFSET
    executed = record.payload("executed") or {}
    assert replica["artifact"] and replica["artifact"] != executed["artifact"]  # un autre entraînement
    assert replica["comparisons"]  # jugé par le Judge, mesures à l'appui
    assert record.promoted is replica["accepted"]
    assert record.decision == ("accept" if replica["accepted"] else "reject")


def test_a_gain_that_does_not_replicate_is_rejected_and_analysed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Branche rare forcée : la réplique n'est PAS acceptée (substitution ciblée du seul résultat
    de réplique ; l'expérience, le Judge et l'analyse sont réels)."""

    def replica_rejected(self: LoopController, plan: Any) -> dict[str, Any]:
        return {
            "seed": plan.recipe.seed + REPLICA_SEED_OFFSET,
            "artifact": None,
            "decision": "reject",
            "accepted": False,
            "reasons": ["suite principale : gain non significatif"],
            "comparisons": [],
        }

    monkeypatch.setattr(LoopController, "_replicate", replica_rejected)
    ctrl = controller(tmp_path / "loop")
    ctrl.run()
    record = next(iter(ctrl.journal.experiments().values()))
    assert (record.payload("judged") or {})["result"]["decision"] == "accept"  # premier verdict
    assert record.decision == "reject" and not record.promoted
    assert ctrl.registry.current() is None
    analysis = record.analysis or {}
    assert analysis["cause"] == FailureCause.NOT_REPLICATED.value and analysis["status"] == "established"
    # La décision finale est reconstruite depuis le journal (reprise) : jamais le premier verdict seul.
    assert ctrl._judge_from_journal(record).accepted is False


def test_replication_can_be_disabled_explicitly(tmp_path: Path) -> None:
    ctrl = controller(
        tmp_path / "loop",
        LoopLimits(**{**LIMITS.__dict__, "replicate_promotions": False}),
    )
    ctrl.run()
    record = next(iter(ctrl.journal.experiments().values()))
    assert record.payload("replicated") is None


def test_a_non_replicated_gain_leads_to_a_stronger_experiment(tmp_path: Path) -> None:
    """La stratégie traite un gain non répliqué comme un effet trop faible : plus de pas."""
    from apex.neural.loop.journal import ExperimentJournal
    from apex.neural.loop.plan import Budget, ExperimentPlan
    from tests.test_loop_components import _observation

    world = support.world()
    obs = _observation(world)
    journal = ExperimentJournal(tmp_path / "j.jsonl")
    policy = StrategyPolicy(finetune_steps=100, finetune_tokens=10_000, max_steps=400, max_tokens=40_000)
    first = policy.decide(obs, world, journal, exhausted=set())
    assert first is not None and first.baseline_recipe is not None
    plan = ExperimentPlan(
        world.fingerprint,
        {"name": "c", "artifact": obs.champion.artifact},  # type: ignore[union-attr]
        first.baseline_recipe,
        first.recipe,
        first.hypothesis,
        Budget(400, 40_000, 60.0),
    )
    journal.experiment(plan.experiment_id, "planned", plan=plan.to_dict())
    journal.experiment(
        plan.experiment_id,
        "analyzed",
        analysis={"cause": FailureCause.NOT_REPLICATED.value, "status": "established"},
    )
    journal.experiment(plan.experiment_id, "promotion", promoted=False)
    decision = policy.decide(obs, world, journal, exhausted=set())
    assert decision is not None and decision.strategy == MORE_STEPS
