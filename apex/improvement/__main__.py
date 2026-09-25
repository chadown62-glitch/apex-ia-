"""Point d'entrée du Continuous Improvement Engine.

    python -m apex.improvement measure --capability C --version V --ledger L
    python -m apex.improvement judge   --capability C --version V --kind K --author A --ledger L
    python -m apex.improvement cycle   --capability C --kind K --index N --before V0 --version V1 \
        --author A --claim improved --hypothesis "..." --change "..." --test "..." --ledger L
    python -m apex.improvement report  --capability generated_solution --ledger <p>

`--before` désigne une version dont un snapshot existe DÉJÀ dans le ledger
(la baseline est mesurée avant la modification, jamais reconstruite après).
Le moteur ne modifie aucun code : l'implémenteur change le code entre
`measure` (avant) et `cycle` (après)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from apex.improvement.engine import CapabilityAdapter, ContinuousImprovementEngine
from apex.improvement.judge import ExternalJudge
from apex.improvement.ledger import ImprovementLedger
from apex.improvement.model import CycleKind, ImplementerClaim, ImprovementVerdict


def _generated_solution() -> tuple[CapabilityAdapter, ExternalJudge]:
    from apex.improvement.capabilities.generated_solution import GeneratedSolutionLoopAdapter, build_judge

    return GeneratedSolutionLoopAdapter(), build_judge()


CAPABILITIES: dict[str, Callable[[], tuple[CapabilityAdapter, ExternalJudge]]] = {
    "generated_solution": _generated_solution,
}


def _engine(args: argparse.Namespace) -> ContinuousImprovementEngine:
    adapter, judge = CAPABILITIES[args.capability]()
    return ContinuousImprovementEngine(adapter, judge, ImprovementLedger(args.ledger))


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def cmd_measure(args: argparse.Namespace) -> int:
    snapshot = _engine(args).measure(args.version)
    _print(
        {
            "version": snapshot.version,
            "reproducible": snapshot.reproducible,
            "blocked": snapshot.blocked,
            "means": {m: snapshot.mean(m) for m in snapshot.samples},
            "notes": list(snapshot.notes),
        }
    )
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    engine = _engine(args)
    gauntlet, _, findings = engine.judge_only(
        CycleKind(args.kind), author_id=args.author, version=args.version
    )
    ImprovementLedger(args.ledger).append_judgement(
        capability_id=engine.spec.capability_id,
        version=args.version,
        kind=args.kind,
        gauntlet=gauntlet.to_dict(),
        findings=[f.to_dict() for f in findings],
    )
    _print(
        {
            "decision": gauntlet.decision.value,
            "veto": gauntlet.veto,
            "probes": {r.judge_id: r.decision.value for r in gauntlet.judge_results},
            "findings": [
                {k: f.to_dict()[k] for k in ("probe_id", "status", "severity", "observed")}
                for f in findings
            ],
        }
    )
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    engine = _engine(args)
    ledger = ImprovementLedger(args.ledger)
    before = ledger.latest_snapshot(engine.spec.capability_id, args.before)
    if before is None:
        print(
            f"aucun snapshot « {args.before} » dans {args.ledger} — mesurer AVANT de modifier",
            file=sys.stderr,
        )
        return 2
    record = engine.run_cycle(
        kind=CycleKind(args.kind),
        cycle_index=args.index,
        before=before,
        version_after=args.version,
        hypothesis=args.hypothesis,
        changes=tuple(args.change or ()),
        tests_executed=tuple(args.test or ()),
        claim=ImplementerClaim(
            author_id=args.author, summary=args.hypothesis, claimed_verdict=ImprovementVerdict(args.claim)
        ),
    )
    _print(
        {
            "cycle_id": record.cycle_id,
            "verdict": record.verdict.value,
            "reason": record.verdict_reason,
            "claim_contradicted": record.claim_contradicted,
            "judge": record.judge["decision"],
            "metrics": [c.to_dict() for c in record.comparisons],
            "findings": [
                {k: f.to_dict()[k] for k in ("probe_id", "status", "severity", "observed")}
                for f in record.findings
            ],
        }
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    ledger = ImprovementLedger(args.ledger)
    adapter, _ = CAPABILITIES[args.capability]()
    capability_id = adapter.spec.capability_id
    _print(
        {
            "snapshots": [
                {
                    "version": s.version,
                    "reproducible": s.reproducible,
                    "means": {m: s.mean(m) for m in s.samples},
                    "blocked": s.blocked,
                }
                for s in ledger.snapshots(capability_id)
            ],
            "judgements": [
                {
                    "version": j["version"],
                    "kind": j["kind"],
                    "decision": j["gauntlet"]["decision"],
                    "findings": [(f["probe_id"], f["status"]) for f in j["findings"]],
                }
                for j in ledger.judgements(capability_id)
            ],
            "cycles": [
                {
                    "index": c["cycle_index"],
                    "kind": c["kind"],
                    "before": c["version_before"],
                    "after": c["version_after"],
                    "verdict": c["verdict"],
                    "judge": c["judge"]["decision"],
                    "claim_contradicted": c["claim_contradicted"],
                }
                for c in ledger.cycles(capability_id)
            ],
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apex.improvement")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("measure", "judge", "cycle", "report"):
        p = sub.add_parser(name)
        p.add_argument("--capability", required=True, choices=sorted(CAPABILITIES))
        p.add_argument("--ledger", required=True)
        if name != "report":
            p.add_argument("--version", required=True)
        if name in ("judge", "cycle"):
            p.add_argument("--kind", required=True, choices=[k.value for k in CycleKind])
            p.add_argument("--author", required=True)
        if name == "cycle":
            p.add_argument("--index", type=int, required=True)
            p.add_argument("--before", required=True)
            p.add_argument("--claim", required=True, choices=[v.value for v in ImprovementVerdict])
            p.add_argument("--hypothesis", required=True)
            p.add_argument("--change", action="append")
            p.add_argument("--test", action="append")
    args = parser.parse_args(argv)
    return {"measure": cmd_measure, "judge": cmd_judge, "cycle": cmd_cycle, "report": cmd_report}[
        args.command
    ](args)


if __name__ == "__main__":
    raise SystemExit(main())
