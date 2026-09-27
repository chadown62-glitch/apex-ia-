"""Protection des capacités acquises : ancres tirées du registre, oubli mesuré contre elles,
promotion destructrice refusée — y compris par dérive cumulée (chaque version ne perd qu'un
peu, dans la tolérance, par rapport à la précédente)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.evaluation import BPB, DocScore, EvalReport
from apex.neural.protection import FORGOTTEN, IMPROVED, NOISE, forgetting, protections
from apex.neural.registry import ModelRegistry, Proposal

CAP = "cap:x:heldout"
GROUPS = {"text": ("heldout",), "x": (CAP,)}
TOLERANCES = {"trace_error": 0.05, BPB: 0.01}


def text_report(level: float, model: str) -> EvalReport:
    scores = tuple(DocScore(f"d{i}", level + 0.01 * (i % 5), 0.5, 100, 20) for i in range(40))
    return EvalReport("heldout", "sha256:text", model, scores)


def cap_report(errors: int, model: str, fingerprint: str = "sha256:cap") -> EvalReport:
    scores = tuple(DocScore(f"t{i}", 1.0 if i < errors else 0.0, 0.0, 10, 4) for i in range(40))
    return EvalReport(CAP, fingerprint, model, scores, metric="trace_error")


def reports(level: float, errors: int, model: str) -> dict[str, EvalReport]:
    return {"heldout": text_report(level, model), CAP: cap_report(errors, model)}


def propose(reg: ModelRegistry, name: str, level: float, errors: int) -> dict:
    return reg.propose(
        Proposal(
            name, f"sha256:{name}", "sha256:data", reports(level, errors, name), "accept", f"art:{name}"
        )
    )


def test_anchor_is_the_best_demonstrated_level_on_the_same_benchmark() -> None:
    entries = [
        {
            "index": 0,
            "name": "a",
            "promoted": True,
            "artifact": "art:a",
            "reports": {k: r.to_dict() for k, r in reports(2.0, 20, "a").items()},
        },
        {
            "index": 1,
            "name": "b",
            "promoted": True,
            "artifact": "art:b",
            "reports": {k: r.to_dict() for k, r in reports(1.8, 22, "b").items()},
        },
        {
            "index": 2,
            "name": "c",
            "promoted": False,
            "artifact": "art:c",
            "reports": {k: r.to_dict() for k, r in reports(1.0, 0, "c").items()},
        },
    ]
    anchors = {p.group: p for p in protections(entries, GROUPS)}
    assert anchors["text"].anchor_index == 1  # meilleur BPB parmi les versions PROMUES
    assert anchors["x"].anchor_index == 0  # meilleure erreur de la capacité
    assert [h["index"] for h in anchors["x"].history] == [0, 1]
    # Une version mesurée sur un autre benchmark n'est pas comparable : exclue.
    entries[0]["reports"][CAP] = cap_report(0, "a", fingerprint="sha256:other").to_dict()
    assert {p.group: p for p in protections(entries, GROUPS)}["x"].anchor_index == 1
    assert protections([], GROUPS) == []


def test_forgetting_distinguishes_forgotten_improved_and_noise() -> None:
    entry = {
        "index": 0,
        "name": "a",
        "promoted": True,
        "artifact": "art:a",
        "reports": {k: r.to_dict() for k, r in reports(2.0, 20, "a").items()},
    }
    (anchor,) = [p for p in protections([entry], GROUPS) if p.group == "x"]

    def status(errors: int) -> tuple[str, float]:
        (row,) = forgetting(anchor, {CAP: cap_report(errors, "n")}, TOLERANCES)
        return row["status"], row["forgetting"]

    assert status(20) == (NOISE, 0.0)
    assert status(21) == (NOISE, 0.025)  # dans la tolérance, non significatif
    assert status(23)[0] == FORGOTTEN  # au-delà de la tolérance (0,075 > 0,05)
    assert status(40)[0] == FORGOTTEN  # dégradation significative
    assert status(8)[0] == IMPROVED
    with pytest.raises(ValueError, match="benchmark"):
        forgetting(anchor, {CAP: cap_report(20, "n", fingerprint="sha256:other")}, TOLERANCES)


def test_cumulative_drift_is_refused_only_with_anchored_protection(tmp_path: Path) -> None:
    """Chaque version améliore le texte (suite principale) et perd UNE tâche sur 40 de la capacité
    (0,025 : dans la tolérance de 0,06, non significatif) par rapport à la précédente. Sans ancre,
    la dérive passe ; avec ancre, la quatrième version (0,575 contre 0,50 démontré) est refusée."""
    for groups, fourth_promoted in ((None, True), (GROUPS, False)):
        reg = ModelRegistry(
            tmp_path / f"{bool(groups)}.jsonl",
            primary="heldout",
            tolerances={"trace_error": 0.06},
            protected_groups=groups,
        )
        assert propose(reg, "v1", 2.0, 20)["promoted"]
        assert propose(reg, "v2", 1.9, 21)["promoted"]
        assert propose(reg, "v3", 1.8, 22)["promoted"]
        fourth = propose(reg, "v4", 1.7, 23)
        assert fourth["promoted"] is fourth_promoted
        if groups:
            assert any("oubli de « x »" in r for r in fourth["reasons"])
            row = next(r for r in fourth["forgetting"] if r["suite"] == CAP)
            assert (row["anchor_index"], row["before"], row["after"]) == (0, 0.5, 0.575)
            assert reg.current()["name"] == "v3"


# -- intégration réelle : boucle, Judge, analyse ------------------------------------


@pytest.fixture(scope="module")
def two_experiments(tmp_path_factory: pytest.TempPathFactory):
    """Boucle RÉELLE (monde réduit de texte) : amorçage promu, puis une expérience jugée avec les
    protections tirées du registre."""
    from apex.neural.loop.controller import LoopController, LoopLimits
    from apex.neural.loop.strategy import StrategyPolicy
    from tests import loop_support as support

    ctrl = LoopController(
        tmp_path_factory.mktemp("protected"),
        support.world(),
        limits=LoopLimits(
            max_cycles=2,
            max_experiments_per_cycle=1,
            max_total_experiments=2,
            max_steps_per_experiment=120,
            max_tokens_per_experiment=30_000,
            checkpoint_every=40,
            experiment_timeout_s=300,
            replicate_promotions=False,
        ),
        policy=StrategyPolicy(finetune_steps=60, finetune_tokens=12_000, max_steps=120),
        bootstrap=support.bootstrap_recipe(steps=80),
        log=lambda _m: None,
    )
    ctrl.run()
    return ctrl


def test_the_judge_measures_forgetting_against_the_anchor(two_experiments) -> None:
    ctrl = two_experiments
    records = list(ctrl.journal.experiments().values())
    assert len(records) == 2 and records[0].promoted
    anchor = ctrl.registry.entries()[0]
    result = (records[1].payload("judged") or {})["result"]
    rows = {r["suite"]: r for r in result["forgetting"]}
    assert set(rows) == set(ctrl.world.protected_groups["text"])  # chaque suite protégée mesurée
    for suite, row in rows.items():
        assert row["anchor_index"] == anchor["index"]
        assert row["before"] == round(anchor["reports"][suite]["value"], 5)
        assert abs(row["forgetting"] - (row["after"] - row["before"])) < 2e-5  # arrondis à 5 décimales
        assert row["status"] in (FORGOTTEN, IMPROVED, NOISE)
    assert result["checks"]["anchor_text"]["ok"]


def test_an_anchor_whose_recorded_measures_are_false_is_refused(two_experiments) -> None:
    """Une ancre dont les mesures enregistrées ne sont plus celles de son checkpoint (registre
    falsifié, ou mesure rejouée sur un autre modèle) invalide le jugement."""
    from dataclasses import replace

    ctrl = two_experiments
    record = list(ctrl.journal.experiments().values())[1]
    executed = record.payload("executed") or {}
    (protection,) = ctrl._protections()
    heldout = protection.reports["heldout"]
    forged_scores = tuple(replace(s, bits_per_byte=s.bits_per_byte * 0.5) for s in heldout.scores)
    forged = replace(
        protection, reports={**protection.reports, "heldout": replace(heldout, scores=forged_scores)}
    )
    verdict = ctrl.judge.judge(
        candidate_artifact=executed["artifact"],
        claimed_reports={n: EvalReport.from_dict(r) for n, r in executed["claimed_reports"].items()},
        training_candidate_ids=executed["training_candidate_ids"],
        baseline_artifact=record.plan["baseline"]["artifact"],
        baseline_seed=record.plan["recipe"]["seed"],
        protections=[forged],
    )
    assert verdict.decision == "reject"
    assert not verdict.checks["anchor_text"]["ok"]
    assert verdict.forgetting == []  # aucune mesure d'oubli contre une ancre invérifiable


def test_a_replica_that_forgets_leads_to_protecting_the_forgotten_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Branche forcée (substitution du SEUL résultat de réplique) : la réplique gagne sur la cible
    mais dégrade une suite protégée. L'analyse conclut à un oubli (pas à un gain trop faible) et la
    stratégie (déjà testée pour cette cause : `protect_domains`) protège le domaine oublié."""
    from typing import Any

    from apex.neural.loop.controller import REPLICA_SEED_OFFSET, LoopController, LoopLimits
    from apex.neural.loop.failure import FailureCause
    from apex.neural.loop.strategy import StrategyPolicy
    from apex.neural.loop.world import PRIMARY
    from tests import loop_support as support

    def forgetful_replica(self: LoopController, plan: Any) -> dict[str, Any]:
        loss = {"verdict": "significant", "bpb_gain_ci": [-0.4, -0.3], "metric": BPB, "n": 20}
        return {
            "seed": plan.recipe.seed + REPLICA_SEED_OFFSET,
            "artifact": None,
            "decision": "reject",
            "accepted": False,
            "reasons": ["régression significative sur « heldout:stdlib »"],
            "comparisons": [
                {
                    "suite": PRIMARY,
                    "baseline_bpb": 3.0,
                    "candidate_bpb": 2.9,
                    **loss,
                    "bpb_gain_ci": [0.05, 0.1],
                },
                {"suite": "heldout:stdlib", "baseline_bpb": 3.0, "candidate_bpb": 3.35, **loss},
            ],
        }

    monkeypatch.setattr(LoopController, "_replicate", forgetful_replica)
    world = support.world()
    ctrl = LoopController(
        tmp_path / "loop",
        world,
        limits=LoopLimits(
            max_cycles=1,
            max_experiments_per_cycle=1,
            max_total_experiments=1,
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
    record = next(iter(ctrl.journal.experiments().values()))
    assert (record.payload("judged") or {})["result"]["decision"] == "accept"
    analysis = record.analysis or {}
    assert analysis["cause"] == FailureCause.PROTECTED_REGRESSION.value
    assert analysis["regressed_suites"] == ["heldout:stdlib"]
    assert all("réplique" in e for e in analysis["evidence"])
