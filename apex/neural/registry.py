"""Registre des versions du modèle APEX : qui est la version en service, et
pourquoi.

Journal JSONL en ajout seul, chaîné par empreintes (même principe que
`apex.knowledge.store`). Chaque proposition est consignée, acceptée ou non :
- sans version en service, un candidat n'est promu que si son expérience a
  montré un gain sur V0 (verdict ACCEPT de son propre Judge) ;
- ensuite, il est comparé à la version en service par `NeuralJudge`, sur les
  MÊMES benchmarks (empreintes identiques, sinon refus) ; seul ACCEPT promeut.
Une version rejetée reste inscrite avec ses raisons : rien n'est effacé."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apex.neural.evaluation import EvalReport, JudgeDecision, NeuralJudge

GENESIS = "sha256:" + "0" * 64


class RegistryCorruptedError(RuntimeError):
    """La chaîne d'empreintes du registre est rompue."""


@dataclass(frozen=True)
class Proposal:
    name: str
    checkpoint_sha256: str
    dataset_fingerprint: str
    reports: dict[str, EvalReport]
    in_run_decision: str


def _digest(entry: dict[str, Any]) -> str:
    payload = json.dumps(
        {k: v for k, v in entry.items() if k != "hash"}, sort_keys=True, ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ModelRegistry:
    def __init__(self, path: Path, *, primary: str = "heldout") -> None:
        self.path = path
        self.primary = primary

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        prev = GENESIS
        for index, entry in enumerate(entries):
            if entry.get("prev") != prev or entry.get("hash") != _digest(entry):
                raise RegistryCorruptedError(f"entrée {index} : chaînage ou contenu modifié")
            prev = entry["hash"]
        return entries

    def current(self) -> dict[str, Any] | None:
        promoted = [e for e in self.entries() if e["promoted"]]
        return promoted[-1] if promoted else None

    def propose(self, proposal: Proposal) -> dict[str, Any]:
        entries = self.entries()
        current = next((e for e in reversed(entries) if e["promoted"]), None)
        if current is None:
            promoted = proposal.in_run_decision == JudgeDecision.ACCEPT.value
            decision = proposal.in_run_decision
            reasons = [
                "première version : promue car son expérience démontre un gain sur V0"
                if promoted
                else f"première version non promue : verdict de son expérience {proposal.in_run_decision}"
            ]
            suites: list[dict[str, Any]] = []
        else:
            baseline = {name: EvalReport.from_dict(r) for name, r in current["reports"].items()}
            verdict = NeuralJudge(primary=self.primary).judge(baseline, proposal.reports)
            promoted = verdict.decision is JudgeDecision.ACCEPT
            decision = verdict.decision.value
            reasons = [f"comparée à « {current['name']} » : {r}" for r in verdict.reasons]
            suites = verdict.to_dict()["suites"]  # type: ignore[assignment]
        entry: dict[str, Any] = {
            "index": len(entries),
            "prev": entries[-1]["hash"] if entries else GENESIS,
            "at": datetime.now(UTC).isoformat(),
            "name": proposal.name,
            "checkpoint_sha256": proposal.checkpoint_sha256,
            "dataset_fingerprint": proposal.dataset_fingerprint,
            "decision": decision,
            "promoted": promoted,
            "reasons": reasons,
            "suites": suites,
            "reports": {name: report.to_dict() for name, report in proposal.reports.items()},
        }
        entry["hash"] = _digest(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
        return entry


def proposal_from_report(path: Path, version: str = "v1", *, exclude: tuple[str, ...] = ()) -> Proposal:
    """Construit une proposition depuis un rapport de `run_experiment`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    reports = {
        name: EvalReport.from_dict(report)
        for name, report in data["evaluations"][version].items()
        if name not in exclude
    }
    return Proposal(
        name=f"{path.stem}:{version}",
        checkpoint_sha256=data["checkpoints"][version],
        dataset_fingerprint=data["dataset"]["fingerprint"],
        reports=reports,
        in_run_decision=data["verdicts"][f"{version}_vs_v0"]["decision"]
        if version == "v1"
        else "inconclusive",
    )
