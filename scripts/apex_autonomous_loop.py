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
from apex.neural.loop.strategy import StrategyPolicy  # noqa: E402
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
    code_refs = LocalDirectoryDiscovery(
        STDLIB_SOURCES, ("*.py",), SourceKind.REPOSITORY, "PSF-2.0"
    ).discover()
    # Un lien symbolique qui sort du répertoire (ex. sitecustomize.py -> /etc) n'appartient pas au domaine.
    code_refs = tuple(r for r in code_refs if r.locator.startswith(CODE_DOMAINS["code"]))
    report = agent.run(PythonDocsDiscovery(modules).discover() + code_refs)
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


def derive_world(args: argparse.Namespace) -> int:
    """Monde ENFANT d'un monde existant : mêmes splits, tokenizer et architecture ; seules les
    nouvelles capacités (et leurs documents) s'ajoutent. Le champion s'adopte ensuite (`adopt`)."""
    from dataclasses import replace

    parent = World.load(args.parent / "world.json")
    added = [c for c in dict.fromkeys(args.add_capability) if c not in parent.spec.capabilities]
    unsupported = [c for c in added if c != "program_trace"]
    if unsupported:
        print(f"capacités non dérivables par cette commande : {unsupported}", file=sys.stderr)
        return 2
    domains = dict(parent.spec.domains)
    train_only = list(parent.spec.train_only_domains)
    capability_data = {}
    if "program_trace" in added:
        from apex.capabilities.program_trace.benchmark import build_benchmark
        from apex.capabilities.program_trace.capability import TASKS_DOMAIN, TASKS_PREFIX
        from apex.capabilities.registry import sandbox_provider

        capability_data["program_trace"] = build_benchmark(sandbox_provider())
        domains[TASKS_DOMAIN] = TASKS_PREFIX
        train_only.append(TASKS_DOMAIN)
    spec = replace(
        parent.spec,
        parent=parent.fingerprint,
        domains=domains,
        train_only_domains=tuple(train_only),
        capabilities=(*parent.spec.capabilities, *added),
    )
    world = World.build(spec, [], capability_data=capability_data, parent=parent)
    fingerprint = world.save(args.workdir / "world.json")
    print(
        json.dumps(
            {
                "world": fingerprint,
                "parent": parent.fingerprint,
                "added_capabilities": added,
                "benchmarks": {n: getattr(b, "report", {}) for n, b in capability_data.items()},
                "capability_decontaminated": len(world.capability_decontaminated),
                "train_by_domain": {d: len(world.train_candidates(d)) for d in spec.domains},
            },
            indent=2,
        )
    )
    return 0


def self_world(args: argparse.Namespace) -> int:
    """Monde ENFANT pour UN bras du protocole P5 : le champion ATTESTÉ du parent tente des problèmes
    program_trace neufs (vérité terrain exécutée dans le sandbox) ; le domaine `self_data` reçoit
    exactement `--docs` documents du bras choisi (étiquettes, tentatives non vérifiées, tentatives
    vérifiées, ou moitié-moitié). Mêmes problèmes tentés pour tous les bras (même graine)."""
    import random
    from dataclasses import replace

    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
    from apex.capabilities.program_trace import self_training
    from apex.capabilities.registry import sandbox_provider
    from apex.neural.loop.execute import load_model_from_artifact

    parent = World.load(args.parent / "world.json")
    loop = LoopController(args.parent, parent, log=lambda _m: None)
    champion = loop.registry.current()
    if champion is None:
        print("le monde parent n'a aucun champion", file=sys.stderr)
        return 2
    problem = loop.attest(champion)
    if problem is not None:
        print(f"champion du parent non attesté : {problem}", file=sys.stderr)
        return 2
    store = LocalFilesystemArtifactStore(root=str(args.parent / "artifacts"))
    model = load_model_from_artifact(store, str(champion["artifact"]), parent)
    _, bench = parent.capabilities["program_trace"]
    rng = random.Random(args.seed)  # noqa: S311 - tirage reproductible
    problems = self_training.fresh_problems(bench, args.count, args.depth, rng)
    attempts = self_training.attempt_and_verify(model, parent.tokenizer, problems, sandbox_provider())
    texts = self_training.arm_texts(attempts, args.arm, args.docs)
    docs = self_training.documents(texts, f"self:program_trace:{args.arm}")
    spec = replace(
        parent.spec,
        parent=parent.fingerprint,
        domains={**parent.spec.domains, self_training.SELF_DOMAIN: self_training.SELF_PREFIX},
        train_only_domains=(*parent.spec.train_only_domains, self_training.SELF_DOMAIN),
    )
    world = World.build(spec, [], parent=parent, train_only=docs)
    fingerprint = world.save(args.workdir / "world.json")
    print(
        json.dumps(
            {
                "world": fingerprint,
                "parent": parent.fingerprint,
                "champion": champion["name"],
                "arm": args.arm,
                "attempts": self_training.summary(attempts),
                "documents": len(docs),
                "document_tokens": sum(len(parent.tokenizer.encode(t)) for t in texts),
                "capability_decontaminated": len(world.capability_decontaminated),
                "train_self_data": len(world.train_candidates(self_training.SELF_DOMAIN)),
            },
            indent=2,
        )
    )
    return 0


def adopt(args: argparse.Namespace) -> int:
    world = World.load(args.workdir / "world.json")
    entry = LoopController(args.workdir, world).adopt(args.parent, reason=args.reason)
    print(
        json.dumps(
            {
                "adopted": entry["name"],
                "artifact": entry["artifact"],
                "reports": {
                    name: r.get("value", r.get("bits_per_byte")) for name, r in entry["reports"].items()
                },
            },
            indent=2,
            ensure_ascii=False,
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
        max_steps_per_experiment=args.max_steps_per_experiment,
        max_tokens_per_experiment=args.max_tokens_per_experiment,
        experiment_timeout_s=args.timeout,
    )
    policy = StrategyPolicy(
        finetune_steps=args.finetune_steps,
        finetune_tokens=args.finetune_tokens,
        finetune_lr=args.finetune_lr,
        max_steps=limits.max_steps_per_experiment,
        max_tokens=limits.max_tokens_per_experiment,
        max_retries=limits.max_retries,
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
    summary = LoopController(args.workdir, world, limits=limits, policy=policy, bootstrap=bootstrap).run()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def directed(args: argparse.Namespace) -> int:
    world = World.load(args.workdir / "world.json")
    limits = LoopLimits(
        max_total_experiments=args.max_total_experiments,
        max_total_steps=args.max_total_steps,
        max_steps_per_experiment=args.max_steps_per_experiment,
        max_tokens_per_experiment=args.max_tokens_per_experiment,
        experiment_timeout_s=args.timeout,
    )
    changes: dict[str, object] = {}
    if args.mix:
        changes["mix"] = _parse_mix(args.mix)
    for key in ("steps", "lr", "tokens", "batch_size", "warmup_steps"):
        if getattr(args, key) is not None:
            changes[key] = getattr(args, key)
    if not changes:
        print("expérience dirigée : aucune modification demandée", file=sys.stderr)
        return 2
    policy = StrategyPolicy(
        finetune_steps=args.reference_steps,
        finetune_tokens=args.reference_tokens,
        finetune_lr=args.reference_lr,
        max_steps=limits.max_steps_per_experiment,
        max_tokens=limits.max_tokens_per_experiment,
        max_retries=limits.max_retries,
    )
    record = LoopController(args.workdir, world, limits=limits, policy=policy).run_directed(
        changes, target=args.target, reason=args.reason
    )
    judged = (record.payload("judged") or {}).get("result", {})
    print(
        json.dumps(
            {
                "experiment": record.experiment_id,
                "decision": record.decision,
                "promoted": record.promoted,
                "learning": judged.get("learning"),
                "analysis": record.analysis,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def fresh_eval(args: argparse.Namespace) -> int:
    """Tâches écrites APRÈS le gel du monde : l'initialisation contre le champion en service,
    par exécution sandbox, bootstrap apparié par fonction (mêmes outils que le Judge)."""
    from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
    from apex.capabilities.code_repair.benchmark import RepairSuite
    from apex.capabilities.code_repair.corpus import ALL
    from apex.capabilities.code_repair.fresh import FRESH_SUITE, build_fresh_suite, fresh_contamination
    from apex.capabilities.code_repair.solver import solve_suite, suite_report
    from apex.capabilities.registry import sandbox_provider
    from apex.neural.dataset import Split
    from apex.neural.evaluation import NeuralJudge
    from apex.neural.loop.execute import initial_model, load_model_from_artifact

    world = World.load(args.workdir / "world.json")
    current = ModelRegistry(args.workdir / "registry.jsonl").current()
    if current is None:
        print("aucun champion au registre", file=sys.stderr)
        return 2
    provider = sandbox_provider()
    built = build_fresh_suite(provider)
    contamination = fresh_contamination(
        [t for split in Split for t in world.dataset.texts(split)], [f.source for f in ALL]
    )
    # Une tâche qui partage une séquence de code avec un texte du monde n'est pas « fraîche » :
    # elle est retirée de l'évaluation (et listée), jamais comptée.
    leaked = set(contamination["shared_12gram_with_world"])
    suite = RepairSuite(built.name, built.split, tuple(i for i in built.instances if i.fid not in leaked))
    store = LocalFilesystemArtifactStore(root=str(args.workdir / "artifacts"))
    models = {
        "init": initial_model(world, world.spec.seed),
        current["name"]: load_model_from_artifact(store, current["artifact"], world),
    }
    reports = {}
    for name, model in models.items():
        model.eval()
        reports[name] = suite_report(suite, solve_suite(model, world.tokenizer, suite, provider), model)
    verdict = NeuralJudge(primary=FRESH_SUITE, tolerances=world.metric_tolerances).judge(
        {FRESH_SUITE: reports["init"]}, {FRESH_SUITE: reports[current["name"]]}
    )
    print(
        json.dumps(
            {
                "suite": FRESH_SUITE,
                "fingerprint": suite.fingerprint,
                "functions": len(suite.functions),
                "bugs": len(suite.instances),
                "contamination": contamination,
                "excluded_as_contaminated": sorted(leaked),
                "models": {name: report.summary() for name, report in reports.items()},
                "comparison": [c.to_dict() for c in verdict.comparisons],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
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
    p.add_argument("--max-steps-per-experiment", type=int, default=800)
    p.add_argument("--max-tokens-per-experiment", type=int, default=240_000)
    p.add_argument("--finetune-steps", type=int, default=200, help="échelle de la recette de référence")
    p.add_argument("--finetune-tokens", type=int, default=60_000)
    p.add_argument("--finetune-lr", type=float, default=1e-3)
    p.add_argument("--timeout", type=float, default=900.0)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=run)
    p = sub.add_parser("directed", help="une expérience posée par l'opérateur, jugée par la boucle")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--target", required=True, help="suite de la faiblesse visée")
    p.add_argument("--reason", required=True, help="la question posée (consignée au journal)")
    p.add_argument("--mix", default="", help="mélange, ex. code_tasks=1.0")
    p.add_argument("--steps", type=int, default=None)
    p.add_argument("--lr", type=float, default=None)
    p.add_argument("--tokens", type=int, default=None)
    p.add_argument("--max-total-experiments", type=int, default=12)
    p.add_argument("--max-total-steps", type=int, default=8000)
    p.add_argument("--max-steps-per-experiment", type=int, default=800)
    p.add_argument("--max-tokens-per-experiment", type=int, default=240_000)
    p.add_argument(
        "--reference-steps", type=int, default=200, help="échelle de la recette de référence (pas)"
    )
    p.add_argument(
        "--reference-tokens", type=int, default=60_000, help="échelle de la recette de référence (tokens)"
    )
    p.add_argument(
        "--reference-lr", type=float, default=1e-3, help="taux d'apprentissage de la recette de référence"
    )
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--warmup-steps", type=int, default=None)
    p.add_argument("--timeout", type=float, default=1800.0)
    p.set_defaults(fn=directed)
    p = sub.add_parser("derive-world", help="monde enfant : hérite splits, tokenizer, architecture")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--add-capability", action="append", default=[])
    p.set_defaults(fn=derive_world)
    p = sub.add_parser("self-world", help="monde enfant nourri des tentatives vérifiées du champion")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--arm", choices=("labeled", "unverified", "verified", "mixed"), required=True)
    p.add_argument("--docs", type=int, default=2000, help="documents d'entraînement du bras")
    p.add_argument("--count", type=int, default=3000, help="problèmes neufs tentés")
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=self_world)
    p = sub.add_parser("adopt", help="adopte le champion attesté du monde parent, re-mesuré ici")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=adopt)
    p = sub.add_parser("fresh-eval", help="tâches écrites après le gel : init contre champion")
    p.add_argument("--workdir", type=Path, required=True)
    p.set_defaults(fn=fresh_eval)
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
