"""Point d'entrée du Continuous Improvement Engine.

    python -m apex.improvement measure --capability C --version V --ledger L
    python -m apex.improvement judge   --capability C --version V --kind K --author A --ledger L
    python -m apex.improvement cycle   --capability C --kind K --index N --before V0 --version V1 \
        --author A --claim improved --hypothesis "..." --change "..." --test "..." --ledger L
    python -m apex.improvement report  --capability generated_solution --ledger <p>
    python -m apex.improvement verify  --capability generated_solution --ledger <p>

`--judge-process` exécute le juge dans un processus séparé ; `--judge-ref R`
(implique `--judge-process`) lit la définition du juge à la révision git R —
typiquement le commit de la baseline, pour que la modification jugée ne
puisse pas réécrire ses propres sondes.

`--before` désigne une version dont un snapshot existe DÉJÀ dans le ledger
(la baseline est mesurée avant la modification, jamais reconstruite après).
Le moteur ne modifie aucun code : l'implémenteur change le code entre
`measure` (avant) et `cycle` (après)."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from apex.improvement.engine import CapabilityAdapter, ContinuousImprovementEngine
from apex.improvement.judge import ExternalJudge, Judge
from apex.improvement.ledger import ImprovementLedger
from apex.improvement.model import CycleKind, ImplementerClaim, ImprovementVerdict
from apex.improvement.remote_judge import SubprocessJudge, encode_assessment


@dataclass(frozen=True)
class CapabilityEntry:
    module: str
    adapter_class: str
    judge_id: str

    @property
    def module_path(self) -> str:
        return self.module.replace(".", "/") + ".py"

    def load(self) -> ModuleType:
        return importlib.import_module(self.module)

    def adapter(self) -> CapabilityAdapter:
        adapter: CapabilityAdapter = getattr(self.load(), self.adapter_class)()
        return adapter

    def in_process_judge(self, module: ModuleType | None = None) -> ExternalJudge:
        judge: ExternalJudge = (module or self.load()).build_judge(self.judge_id)
        return judge


CAPABILITIES: dict[str, CapabilityEntry] = {
    "generated_solution": CapabilityEntry(
        module="apex.improvement.capabilities.generated_solution",
        adapter_class="GeneratedSolutionLoopAdapter",
        judge_id="ci-judge:generated-solution",
    ),
    "model_routing": CapabilityEntry(
        module="apex.improvement.capabilities.model_routing",
        adapter_class="ModelRoutingAdapter",
        judge_id="ci-judge:model-routing",
    ),
    "knowledge_curation": CapabilityEntry(
        module="apex.improvement.capabilities.knowledge_curation",
        adapter_class="KnowledgeCurationAdapter",
        judge_id="ci-judge:knowledge-curation",
    ),
    "native_learning": CapabilityEntry(
        module="apex.improvement.capabilities.native_learning",
        adapter_class="NativeLearningAdapter",
        judge_id="ci-judge:native-learning",
    ),
    "desktop_distribution": CapabilityEntry(
        module="apex.improvement.capabilities.desktop_distribution",
        adapter_class="DesktopDistributionAdapter",
        judge_id="ci-judge:desktop-distribution",
    ),
    "real_windows": CapabilityEntry(
        module="apex.improvement.capabilities.real_windows",
        adapter_class="RealWindowsAdapter",
        judge_id="ci-judge:real-windows",
    ),
}


def _judge(args: argparse.Namespace) -> Judge:
    entry = CAPABILITIES[args.capability]
    judge_ref = getattr(args, "judge_ref", None)
    if judge_ref or getattr(args, "judge_process", False):
        return SubprocessJudge(
            entry.judge_id, capability=args.capability, module_path=entry.module_path, judge_ref=judge_ref
        )
    return entry.in_process_judge()


def _engine(args: argparse.Namespace) -> ContinuousImprovementEngine:
    entry = CAPABILITIES[args.capability]
    return ContinuousImprovementEngine(entry.adapter(), _judge(args), ImprovementLedger(args.ledger))


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
    gauntlet, assessment = engine.judge_only(
        CycleKind(args.kind), author_id=args.author, version=args.version
    )
    findings = assessment.findings
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
            "judge_provenance": record.judge_provenance,
            "metrics": [c.to_dict() for c in record.comparisons],
            "findings": [
                {k: f.to_dict()[k] for k in ("probe_id", "status", "severity", "observed")}
                for f in record.findings
            ],
        }
    )
    return 0


def cmd_judge_worker(args: argparse.Namespace) -> int:
    """Côté processus séparé de `SubprocessJudge` — imprime UNE ligne de résultat."""
    entry = CAPABILITIES[args.capability]
    module = None
    if args.judge_module:
        spec = importlib.util.spec_from_file_location("apex_pinned_judge", args.judge_module)
        if spec is None or spec.loader is None:
            print(f"module de juge illisible : {args.judge_module}", file=sys.stderr)
            return 2
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # requis par @dataclass pendant l'exécution du module
        spec.loader.exec_module(module)
    judge = entry.in_process_judge(module)
    if judge.judge_id != args.judge_id:
        print(f"juge « {judge.judge_id} » au lieu de « {args.judge_id} »", file=sys.stderr)
        return 2
    assessment = judge.assess(
        CycleKind(args.kind),
        author_id=args.author,
        capability_id=entry.adapter().spec.capability_id,
        cycle_index=args.cycle_index,
        version=args.version,
        cycle_id=args.cycle_id,
    )
    print(encode_assessment(assessment))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    problems = ImprovementLedger(args.ledger).verify()
    _print({"ledger": args.ledger, "intact": not problems, "problems": problems})
    return 0 if not problems else 1


def cmd_report(args: argparse.Namespace) -> int:
    ledger = ImprovementLedger(args.ledger)
    capability_id = CAPABILITIES[args.capability].adapter().spec.capability_id
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
    for name in ("measure", "judge", "cycle", "report", "verify", "judge-worker"):
        p = sub.add_parser(name)
        p.add_argument("--capability", required=True, choices=sorted(CAPABILITIES))
        if name != "judge-worker":
            p.add_argument("--ledger", required=True)
        if name not in ("report", "verify"):
            p.add_argument("--version", required=True)
        if name in ("judge", "cycle", "judge-worker"):
            p.add_argument("--kind", required=True, choices=[k.value for k in CycleKind])
            p.add_argument("--author", required=True)
        if name in ("judge", "cycle"):
            p.add_argument("--judge-process", action="store_true")
            p.add_argument("--judge-ref")
        if name == "judge-worker":
            p.add_argument("--cycle-index", type=int, required=True)
            p.add_argument("--cycle-id", required=True)
            p.add_argument("--judge-id", required=True)
            p.add_argument("--judge-module")
        if name == "cycle":
            p.add_argument("--index", type=int, required=True)
            p.add_argument("--before", required=True)
            p.add_argument("--claim", required=True, choices=[v.value for v in ImprovementVerdict])
            p.add_argument("--hypothesis", required=True)
            p.add_argument("--change", action="append")
            p.add_argument("--test", action="append")
    args = parser.parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "measure": cmd_measure,
        "judge": cmd_judge,
        "cycle": cmd_cycle,
        "report": cmd_report,
        "verify": cmd_verify,
        "judge-worker": cmd_judge_worker,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
