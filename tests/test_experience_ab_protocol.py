"""Analyse du protocole RUN 1 -> RUN 2 (`scripts/apex_experience_ab.py`) : fonctions pures, données saisies à la
main ici uniquement pour vérifier le CALCUL — jamais un résultat d'expérience."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "apex_experience_ab.py"
_spec = importlib.util.spec_from_file_location("apex_experience_ab", _PATH)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


def test_score_reads_only_what_the_oracle_reported() -> None:
    assert ab.score("fail", "3/12 cas incorrects (indices [8, 9, 10])") == (9, 12)
    assert ab.score("pass", "12/12 résultats conformes aux valeurs attendues (comparées hors sandbox)") == (
        12,
        12,
    )
    assert ab.score("fail", "le script n'a pas terminé normalement — x") == (0, None)
    assert ab.score(None, None) == (None, None)


def test_sign_test_is_exact_and_two_sided() -> None:
    assert ab.sign_test_p(0, 0) is None
    assert ab.sign_test_p(6, 0) == pytest.approx(2 / 64)
    assert ab.sign_test_p(3, 3) == 1.0
    assert ab.sign_test_p(0, 6) == ab.sign_test_p(6, 0)


def _row(kind, rep, arm, verdict, passed, source="e1", fingerprint=None):
    return {
        "kind": kind,
        "replicate": rep,
        "arm": arm,
        "oracle_verdict": verdict,
        "cases_passed": passed,
        "source_experience_id": source if arm == "conditioned" else None,
        "fingerprint": fingerprint or f"{kind}{rep}{arm}",
    }


def test_only_pairs_whose_run1_failed_and_whose_strategy_came_from_an_experience_count() -> None:
    rows = [
        _row("a", 1, "run1", "fail", 9),
        _row("a", 1, "baseline", "fail", 9),
        _row("a", 1, "conditioned", "pass", 12),
        _row("b", 1, "run1", "pass", 12),
        _row("b", 1, "baseline", "pass", 12),
        _row("b", 1, "conditioned", "pass", 12),
        _row("c", 1, "run1", "fail", 3),
        _row("c", 1, "baseline", "fail", 3),
        _row("c", 1, "conditioned", "fail", 3, source=None),
    ]
    summary = ab.analyse(rows)
    assert summary["n_pairs"] == 1 and summary["conditioned_better"] == 1
    reasons = {e["kind"]: e["reason"] for e in summary["excluded"]}
    assert "RUN 1 réussi" in reasons["b"] and "sans expérience source" in reasons["c"]


def test_a_regression_is_reported_as_such_never_hidden() -> None:
    rows = []
    for rep in range(1, 7):
        rows += [
            _row("a", rep, "run1", "fail", 9),
            _row("a", rep, "baseline", "fail", 9),
            _row("a", rep, "conditioned", "fail", 4),
        ]
    summary = ab.analyse(rows)
    assert summary["conditioned_worse"] == 6 and summary["conclusion"].startswith("RÉGRESSION")


def test_no_difference_is_the_honest_default() -> None:
    rows = [
        _row("a", 1, "run1", "fail", 9),
        _row("a", 1, "baseline", "fail", 9),
        _row("a", 1, "conditioned", "pass", 12),
    ]
    assert ab.analyse(rows)["conclusion"].startswith("AUCUNE")  # 1 paire : p = 1
