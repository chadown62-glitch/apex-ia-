"""Boucle autonome d'amélioration du modèle APEX (Priorité 1).

# 1. geler le monde (données réelles via le Knowledge Agent)
.venv/bin/python scripts/apex_autonomous_loop.py init-world --workdir W
# 2. lancer la boucle bornée (amorçage d'un champion si le registre est vide)
.venv/bin/python scripts/apex_autonomous_loop.py run --workdir W --bootstrap stdlib=1.0
# 3. suivre / arrêter / revenir en arrière
.venv/bin/python scripts/apex_autonomous_loop.py status --workdir W
.venv/bin/python scripts/apex_autonomous_loop.py stop --workdir W
.venv/bin/python scripts/apex_autonomous_loop.py rollback --workdir W --reason "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from apex.knowledge.acquisition import (  # noqa: E402
    FileAcquirer,
    LocalDirectoryDiscovery,
    PythonDocsAcquirer,
    PythonDocsDiscovery,
)
from apex.knowledge.export import to_training_candidates  # noqa: E402
from apex.knowledge.model import SourceKind  # noqa: E402
from apex.knowledge.pipeline import AgentConfig, KnowledgeAgent  # noqa: E402
from apex.neural.experiment import STDLIB_MODULES  # noqa: E402
from apex.neural.loop.controller import LoopController, LoopLimits, workdir_lock  # noqa: E402
from apex.neural.loop.plan import Recipe  # noqa: E402
from apex.neural.loop.world import World, WorldSpec  # noqa: E402
from apex.neural.registry import ModelRegistry  # noqa: E402

DOMAINS = {"stdlib": "pydoc:", "apex_docs": "file://"}


STDLIB_SOURCES = Path("/usr/lib/python3.12")
CODE_DOMAINS = {
    "stdlib": "pydoc:",
    "code": f"file://{STDLIB_SOURCES}/",
    "code_tasks": "task:code_repair/",
}


def init_code_world(args: argparse.Namespace) -> int:
    """Monde de la capacité CODE REPAIR : docstrings (stdlib), sources Python
    de la bibliothèque standard (code, via le chemin code du Knowledge Agent),
    solutions des tâches TRAIN (code_tasks, train seulement) et benchmark
    construit par exécution réelle dans le sandbox."""
    from apex.capabilities.code_repair.benchmark import build_benchmark
    from apex.capabilities.code_repair.corpus import ALL
    from apex.capabilities.registry import sandbox_provider

    bench = build_benchmark(ALL, sandbox_provider())
    modules = STDLIB_MODULES[: args.modules] if args.modules else STDLIB_MODULES
    agent = KnowledgeAgent(
        [PythonDocsAcquirer(), FileAcquirer()], AgentConfig(chunk_words=args.chunk_words)
    )
    refs = (
        PythonDocsDiscovery(modules).discover()
        + LocalDirectoryDiscovery(STDLIB_SOURCES, ("*.py",), SourceKind.REPOSITORY, "PSF-2.0").discover()
    )
    report = agent.run(refs)
    candidates = to_training_candidates(report.items, chunk_words=args.chunk_words)
    spec = WorldSpec(
        domains=CODE_DOMAINS,
        vocab_size=args.vocab,
        context_length=args.context,
        seed=args.seed,
        capabilities=("code_repair",),
        train_only_domains=("code_tasks",),
    )
    world = World.build(spec, candidates, capability_data={"code_repair": bench})
    fingerprint = world.save(args.workdir / "world.json")
    print(
        json.dumps(
            {
                "world": fingerprint,
                "knowledge": report.decisions,
                "candidates": len(candidates),
                "capability_benchmark": {"fingerprint": bench.fingerprint, **bench.report},
                "capability_decontaminated": list(world.capability_decontaminated),
                "suites": sorted(world.suite_names),
                "train_by_domain": {d: len(world.train_candidates(d)) for d in CODE_DOMAINS},
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def init_world(args: argparse.Namespace) -> int:
    if args.capability == "code_repair":
        return init_code_world(args)
    modules = STDLIB_MODULES[: args.modules] if args.modules else STDLIB_MODULES
    agent = KnowledgeAgent(
        [PythonDocsAcquirer(), FileAcquirer()], AgentConfig(chunk_words=args.chunk_words)
    )
    refs = (
        PythonDocsDiscovery(modules).discover()
        + LocalDirectoryDiscovery(REPO / "docs", ("*.md",), SourceKind.DOCUMENTATION, "apex-own").discover()
    )
    report = agent.run(refs)
    candidates = to_training_candidates(report.items, chunk_words=args.chunk_words)
    world = World.build(WorldSpec(domains=DOMAINS, vocab_size=args.vocab, seed=args.seed), candidates)
    fingerprint = world.save(args.workdir / "world.json")
    print(
        json.dumps(
            {
                "world": fingerprint,
                "knowledge": report.decisions,
                "candidates": len(candidates),
                "benchmarks": {n: len(b.documents) for n, b in world.benchmarks.items()},
                "train_by_domain": {d: len(world.train_candidates(d)) for d in DOMAINS},
            },
            indent=2,
        )
    )
    return 0


def _parse_mix(text: str) -> dict[str, float]:
    return {k: float(v) for k, v in (part.split("=") for part in text.split(","))}


def run(args: argparse.Namespace) -> int:
    world = World.load(args.workdir / "world.json")
    limits = LoopLimits(
        max_cycles=args.max_cycles,
        max_experiments_per_cycle=args.max_experiments_per_cycle,
        max_total_experiments=args.max_total_experiments,
        max_total_steps=args.max_total_steps,
        experiment_timeout_s=args.timeout,
    )
    bootstrap = (
        Recipe(
            init="scratch",
            mix=_parse_mix(args.bootstrap),
            steps=args.bootstrap_steps,
            lr=3e-3,
            tokens=args.bootstrap_tokens,
            seed=args.seed,
        )
        if args.bootstrap
        else None
    )
    summary = LoopController(args.workdir, world, limits=limits, bootstrap=bootstrap).run()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def status(args: argparse.Namespace) -> int:
    from apex.neural.loop.journal import ExperimentJournal

    journal = ExperimentJournal(args.workdir / "journal.jsonl")
    registry = ModelRegistry(args.workdir / "registry.jsonl")
    rows = []
    for record in journal.experiments().values():
        judged = record.payload("judged") or {}
        rows.append(
            {
                "experiment": record.experiment_id,
                "parent": record.plan.get("parent_experiment_id"),
                "strategy": record.plan.get("hypothesis", {}).get("strategy"),
                "status": record.status,
                "decision": record.decision,
                "cause": (record.analysis or {}).get("cause"),
                "promoted": record.promoted,
                "heldout_bpb": judged.get("result", {})
                .get("candidate", {})
                .get("heldout", {})
                .get("bits_per_byte"),
                "capability": {
                    name: summary.get("value")
                    for name, summary in judged.get("result", {}).get("candidate", {}).items()
                    if name.startswith("cap:")
                },
                "primary": judged.get("result", {}).get("primary"),
            }
        )
    current = registry.current()
    print(
        json.dumps(
            {
                "champion": current["name"] if current else None,
                "experiments": rows,
                "journal_entries": len(journal.entries()),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def stop(args: argparse.Namespace) -> int:
    (args.workdir / "STOP").write_text("arrêt demandé\n", encoding="utf-8")
    print("fichier STOP créé : la boucle s'arrêtera proprement au prochain pas")
    return 0


def rollback(args: argparse.Namespace) -> int:
    with workdir_lock(args.workdir):  # refuse si une boucle écrit dans le registre
        entry = ModelRegistry(args.workdir / "registry.jsonl").rollback(args.reason)
    print(
        json.dumps({"champion": entry["name"], "reasons": entry["reasons"]}, indent=2, ensure_ascii=False)
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init-world")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--modules", type=int, default=0)
    p.add_argument("--chunk-words", type=int, default=120)
    p.add_argument("--vocab", type=int, default=512)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--capability", choices=("code_repair",), default=None)
    p.add_argument("--context", type=int, default=128)
    p.set_defaults(fn=init_world)
    p = sub.add_parser("run")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--bootstrap", default="", help="mélange d'amorçage, ex. stdlib=1.0")
    p.add_argument("--bootstrap-steps", type=int, default=400)
    p.add_argument("--bootstrap-tokens", type=int, default=120_000)
    p.add_argument("--max-cycles", type=int, default=3)
    p.add_argument("--max-experiments-per-cycle", type=int, default=3)
    p.add_argument("--max-total-experiments", type=int, default=8)
    p.add_argument("--max-total-steps", type=int, default=6000)
    p.add_argument("--timeout", type=float, default=900.0)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=run)
    for name, fn in (("status", status), ("stop", stop)):
        p = sub.add_parser(name)
        p.add_argument("--workdir", type=Path, required=True)
        p.set_defaults(fn=fn)
    p = sub.add_parser("rollback")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=rollback)
    args = parser.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
