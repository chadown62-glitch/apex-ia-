"""Verdict multi-graines (P3.7) : règles pré-enregistrées, sur des mesures construites."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.evaluation import BPB, DocScore, EvalReport
from apex.neural.protection import Protection
from apex.neural.variance import (
    ACCEPT,
    IMPROVEMENT,
    INCONCLUSIVE,
    INVALID,
    MEMORIZATION,
    NO_REGRESSION,
    REGRESSION,
    REJECT,
    VARIANCE_INCONCLUSIVE,
    failed_integrity,
    pooled_sigma,
    seed_verdict,
    suite_row,
)

N = 200
TASK = "order_error"
TOLERANCES = {TASK: 0.05, BPB: 0.01}


def task_report(
    errors: int, name: str = "cap:x:heldout", *, n: int = N, fingerprint: str = "fp"
) -> EvalReport:
    """`errors` éléments ratés (1.0) sur `n`, les premiers."""
    scores = tuple(DocScore(f"{name}#{i}", 1.0 if i < errors else 0.0, 0.0, 10, 3) for i in range(n))
    return EvalReport(name, fingerprint, "m", scores, metric=TASK)


def protection(group: str, reports: dict[str, EvalReport]) -> Protection:
    return Protection(group, tuple(reports), 0, "champion", "sha256:a", reports, ())


def test_pooled_sigma_is_the_pooled_seed_standard_deviation() -> None:
    sigma = pooled_sigma([{"s": [0.4, 0.6]}, {"s": [0.1, 0.2, 0.3]}, {"s": [0.9]}])
    # paire : 0,02 (1 ddl) ; triplet : 0,02 (2 ddl) ; singleton ignoré
    assert sigma["s"] == pytest.approx(math.sqrt((0.02 + 0.02) / 3))


def test_bpb_deltas_use_the_byte_weighted_aggregate() -> None:
    def text(values: list[float]) -> EvalReport:
        scores = (DocScore("a", values[0], 0.5, 1000, 10), DocScore("b", values[1], 0.5, 10, 3))
        return EvalReport("heldout", "fp", "m", scores)

    row = suite_row(
        "text", text([2.0, 2.0]), [text([2.0, 3.0]), text([2.0, 3.0])], sigma_prior=None, tolerance=0.01
    )
    # moyenne simple : +0,5 ; agrégat pondéré (celui d'EvalReport) : +10/1010
    assert row["delta"] == pytest.approx(10 / 1010, abs=1e-5)


def test_one_losing_seed_is_never_confirmed_forgetting() -> None:
    reference = task_report(100)
    row = suite_row("g", reference, [task_report(140), task_report(98)], sigma_prior=0.1, tolerance=0.05)
    assert row["status"] != REGRESSION  # une graine perd beaucoup, l'autre non : pas d'oubli confirmé


def test_reproduced_degradation_beyond_the_margin_is_a_regression() -> None:
    reference = task_report(40)
    row = suite_row("g", reference, [task_report(160), task_report(170)], sigma_prior=0.05, tolerance=0.05)
    assert row["status"] == REGRESSION and all(d > 0 for d in row["deltas"])


def test_improvement_and_no_regression_and_inconclusive() -> None:
    reference = task_report(100)
    assert (
        suite_row("g", reference, [task_report(20), task_report(25)], sigma_prior=0.02, tolerance=0.05)[
            "status"
        ]
        == IMPROVEMENT
    )
    same = suite_row("g", reference, [task_report(100), task_report(100)], sigma_prior=0.0, tolerance=0.05)
    assert same["status"] == NO_REGRESSION
    # Seed noise large devant la marge : impossible de conclure dans un sens ou dans l'autre.
    noisy = suite_row("g", reference, [task_report(105), task_report(115)], sigma_prior=0.1, tolerance=0.05)
    assert noisy["status"] == VARIANCE_INCONCLUSIVE


def test_the_margin_comes_from_the_prior_never_from_the_candidate_seeds() -> None:
    reference = task_report(100)
    row = suite_row("g", reference, [task_report(80), task_report(140)], sigma_prior=None, tolerance=0.05)
    assert row["margin"] == 0.05 and row["sigma_observed"] > 0.05  # graines dispersées : marge inchangée


def _verdict(target_errors: list[int], guard_errors: list[int], **over: object) -> dict:
    target_ref = {"cap:x:heldout": task_report(N), "cap:x:train": task_report(N, "cap:x:train")}
    guard_ref = {"cap:g:heldout": task_report(60, "cap:g:heldout")}
    seeds = [
        {
            "cap:x:heldout": task_report(t),
            "cap:x:train": task_report(over.pop("train", t) if i == 0 else t, "cap:x:train"),  # type: ignore[arg-type]
            "cap:g:heldout": task_report(g, "cap:g:heldout"),
        }
        for i, (t, g) in enumerate(zip(target_errors, guard_errors, strict=True))
    ]
    kwargs = {
        "seeds": seeds,
        "protections": [protection("x", target_ref), protection("g", guard_ref)],
        "tolerances": TOLERANCES,
        "prior": {"cap:g:heldout": 0.02},
        "target": "cap:x:heldout",
        "memorization_suite": "cap:x:train",
        "new_capability": True,
        "invalid": [],
    } | over
    return seed_verdict(**kwargs)  # type: ignore[arg-type]


def test_seed_verdict_decisions() -> None:
    accepted = _verdict([60, 70], [60, 61])
    assert accepted["decision"] == ACCEPT and accepted["status"] == IMPROVEMENT
    regressed = _verdict([60, 70], [140, 150])
    assert regressed["decision"] == REJECT and "régression" in regressed["reasons"][0]
    not_acquired = _verdict([185, 186], [60, 60])  # gain réel mais exactitude < seuil
    assert not_acquired["decision"] == REJECT and "non acquise" in not_acquired["reasons"][0]
    invalid = _verdict([60, 70], [60, 61], invalid=["training_replayed"])
    assert invalid["decision"] == INVALID and invalid["rows"] == []
    no_gain = _verdict([N, N], [60, 60])
    assert no_gain["decision"] == REJECT and "aucun gain" in no_gain["reasons"][0]


def test_memorization_and_inconclusive_ask_for_the_right_thing() -> None:
    target_ref = {"cap:x:heldout": task_report(N), "cap:x:train": task_report(N, "cap:x:train")}
    seeds = [
        {"cap:x:heldout": task_report(N), "cap:x:train": task_report(20, "cap:x:train")} for _ in range(2)
    ]
    memorized = seed_verdict(
        seeds=seeds,
        protections=[protection("x", target_ref)],
        tolerances=TOLERANCES,
        prior={},
        target="cap:x:heldout",
        memorization_suite="cap:x:train",
        new_capability=True,
        invalid=[],
    )
    assert memorized["decision"] == REJECT and memorized["status"] == MEMORIZATION
    inconclusive = _verdict([60, 70], [66, 80], prior={"cap:g:heldout": 0.08})
    assert inconclusive["decision"] == INCONCLUSIVE and inconclusive["need_more_seeds"]
    three = _verdict([60, 70, 65], [66, 80, 75], prior={"cap:g:heldout": 0.08})
    assert three["decision"] == INCONCLUSIVE and not three["need_more_seeds"]  # k = 3 : on s'arrête


def test_a_seed_far_beyond_the_margin_never_leads_to_acceptance() -> None:
    """Propriété vérifiée par le RÉSULTAT : avec k >= 2, l'incertitude inclut l'écart observé entre
    graines, donc une graine au-delà de la marge rend déjà la ligne REGRESSION ou
    VARIANCE_INCONCLUSIVE ; la condition explicite sur l'artefact promu est une défense en
    profondeur (mutant équivalent, documenté)."""
    # Graine 0 (l'artefact promu) très dégradée, graine 1 très bonne : la moyenne passe, pas l'artefact.
    result = _verdict([60, 70], [95, 20], prior={"cap:g:heldout": 0.0})
    assert result["decision"] != ACCEPT


def test_integrity_failures_are_separated_from_hypothesis_failures() -> None:
    checks = {
        "training_replayed": {"ok": False},
        "anchor_text": {"ok": False},
        "candidate_reproducible": {"ok": True},
        "something_statistical": {"ok": False},
    }
    assert failed_integrity(checks) == ["anchor_text", "training_replayed"]
