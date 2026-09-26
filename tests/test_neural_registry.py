"""Registre des versions : promotion uniquement sur ACCEPT du Judge, tout
est consigné, la chaîne détecte une réécriture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="PyTorch absent (extra « neural » non installé)")

from apex.neural.evaluation import DocScore, EvalReport
from apex.neural.registry import ModelRegistry, Proposal, RegistryCorruptedError


def reports(values: list[float], fingerprint: str = "sha256:b") -> dict[str, EvalReport]:
    return {
        "heldout": EvalReport(
            "heldout",
            fingerprint,
            "sha256:m",
            tuple(DocScore(f"d{i}", v, 0.5, 100, 30) for i, v in enumerate(values)),
        )
    }


BASE = [2.0 + 0.01 * i for i in range(10)]


def proposal(
    name: str, values: list[float], in_run: str = "accept", fingerprint: str = "sha256:b"
) -> Proposal:
    return Proposal(name, "sha", "sha256:data", reports(values, fingerprint), in_run)


def test_promotion_requires_the_judge(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "r.jsonl")
    assert registry.current() is None
    assert registry.propose(proposal("unproven", BASE, in_run="inconclusive"))["promoted"] is False
    assert registry.current() is None
    assert registry.propose(proposal("first", BASE))["promoted"] is True
    worse = registry.propose(proposal("worse", [v + 0.5 for v in BASE]))
    assert worse["promoted"] is False and worse["decision"] == "reject"
    noise = registry.propose(
        proposal("noise", [v + (0.001 if i % 2 else -0.001) for i, v in enumerate(BASE)])
    )
    assert noise["promoted"] is False and noise["decision"] == "inconclusive"
    other_benchmark = registry.propose(proposal("other", [v - 1 for v in BASE], fingerprint="sha256:x"))
    assert other_benchmark["promoted"] is False
    better = registry.propose(proposal("better", [v - 0.5 for v in BASE]))
    assert better["promoted"] is True
    assert registry.current()["name"] == "better"  # type: ignore[index]
    assert [e["name"] for e in registry.entries()] == [
        "unproven",
        "first",
        "worse",
        "noise",
        "other",
        "better",
    ]


def test_rewriting_history_is_detected(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "r.jsonl")
    registry.propose(proposal("first", BASE))
    registry.propose(proposal("worse", [v + 0.5 for v in BASE]))
    lines = registry.path.read_text().splitlines()
    forged = json.loads(lines[1])
    forged["promoted"] = True
    registry.path.write_text(lines[0] + "\n" + json.dumps(forged) + "\n")
    with pytest.raises(RegistryCorruptedError, match="entrée 1"):
        registry.current()
