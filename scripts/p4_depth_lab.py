"""P4 — mesure contrôlée de la généralisation en PROFONDEUR de `program_trace`.

Un bras = un facteur changé par rapport au bras `base`, à budget égal (même nombre de pas, même
taille de lot, même fenêtre : même nombre de jetons vus) :
- `base`       : architecture du cerveau, format de trace standard (`a3,b7,c4=4`), profondeurs 1-3 ;
- `big`        : modèle plus grand (≈ 4x paramètres), le reste identique ;
- `explicit`   : chaque étape recopie l'instruction exécutée avant sa valeur (`a=3>3,b=a+4>7,…=4`) :
  la fin de trace et la dépendance à lire sont explicites dans le texte déjà écrit ;
- `curriculum` : une part des problèmes d'entraînement est de profondeur 4.

Évaluation (mêmes problèmes pour tous les bras) : profondeur 3 = suite held-out protégée du
benchmark, profondeur 4 = suite `deeper` protégée, profondeur 5 = 200 problèmes neufs (forme
canonique absente de l'entraînement et de l'évaluation, noms tirés au hasard).

CRITÈRE PRÉ-ENREGISTRÉ (fixé avant toute exécution) : un bras RÉUSSIT la profondeur d si
l'exactitude de trace sur la suite de profondeur d a une borne inférieure de l'IC bootstrap à 95 %
> 0,10. La généralisation en longueur n'est établie qu'à une profondeur JAMAIS entraînée (5 pour
tous les bras ; 4 aussi, sauf pour `curriculum`). La profondeur 3 ne doit pas régresser par rapport à
`base` (écart apparié non significativement négatif)."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apex.capabilities.program_trace.benchmark import suite_name
from apex.capabilities.program_trace.capability import score_output
from apex.capabilities.program_trace.generator import Problem, generate
from apex.neural.config import ModelConfig, OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.loop.world import World
from apex.neural.model import ApexLM, generate_batch
from apex.neural.training import TokenBatcher, TrainingEngine
from apex.stattest import StatTest

ARMS = ("base", "big", "explicit", "curriculum")
_EXPLICIT = re.compile(r"((?:[a-z]=[^,>]+>-?\d+,)*[a-z]=[^,>]+>-?\d+)=(-?\d+)")


def explicit_answer(problem: Problem) -> str:
    steps = problem.program.split(";")
    items = ",".join(f"{s}>{v}" for s, v in zip(steps, problem.values, strict=True))
    return f"{items}={problem.values[-1]}"


def score_explicit(problem: Problem, text: str) -> dict[str, object]:
    match = _EXPLICIT.fullmatch(text)
    if match is None:
        return {"exact": False, "well_formed": False, "final_ok": False, "written": -1, "copied_ok": False}
    items = match.group(1).split(",")
    values = [int(item.rsplit(">", 1)[1]) for item in items]
    copied = [item.rsplit(">", 1)[0] for item in items]
    final = int(match.group(2))
    return {
        "exact": values == list(problem.values) and final == problem.values[-1],
        "well_formed": True,
        "final_ok": final == problem.values[-1],
        "written": len(values),
        "copied_ok": copied == problem.program.split(";"),
    }


def document(problem: Problem, arm: str) -> str:
    return problem.prompt + (explicit_answer(problem) if arm == "explicit" else problem.answer)


def config(world: World, arm: str) -> ModelConfig:
    base = world.spec.model_config(world.tokenizer.vocab_size)
    if arm != "big":
        return base
    return ModelConfig(
        vocab_size=base.vocab_size,
        hidden_size=base.hidden_size * 3 // 2,
        num_layers=base.num_layers * 2,
        num_heads=base.num_heads * 3 // 2,
        intermediate_size=base.intermediate_size * 3 // 2,
        context_length=base.context_length,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--steps", type=int, default=6000)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.time()
    world = World.load(args.world)
    tokenizer = world.tokenizer
    _, bench = world.capabilities["program_trace"]
    evaluated = {
        3: bench.suites[suite_name("heldout")].problems,
        4: bench.suites[suite_name("deeper")].problems,
    }
    known = {p.canonical for p in bench.pool} | {p.canonical for p in bench.eval_problems()}
    rng = random.Random("p4-depth5")  # noqa: S311 - même profondeur 5 pour tous les bras
    depth5: list[Problem] = []
    while len(depth5) < 200:
        p = generate(5, rng)
        if p.canonical not in known:
            known.add(p.canonical)
            depth5.append(p)
    evaluated[5] = tuple(depth5)

    train = list(bench.pool)
    if args.arm == "curriculum":
        drng = random.Random(f"p4-curriculum-{args.seed}")  # noqa: S311
        extra: list[Problem] = []
        while len(extra) < len(train) // 4:
            p = generate(4, drng)
            if p.canonical not in known:
                known.add(p.canonical)
                extra.append(p)
        keep = random.Random(args.seed).sample(train, len(train) - len(extra))  # noqa: S311
        train = [*keep, *extra]
    order = random.Random(args.seed).sample(train, len(train))  # noqa: S311
    stream: list[int] = []
    for p in order:
        stream += tokenizer.encode(document(p, args.arm), add_bos=True, add_eos=True)

    torch.manual_seed(args.seed)
    model = ApexLM(config(world, args.arm))
    training = TrainingConfig(
        max_steps=args.steps,
        batch_size=args.batch_size,
        seed=args.seed,
        optimizer=OptimizerSpec(lr=1e-3, weight_decay=0.1),
        scheduler=SchedulerSpec(name="cosine", warmup_steps=100, min_lr_ratio=0.1),
    )
    batcher = TokenBatcher(
        stream, window=world.spec.context_length, batch_size=args.batch_size, seed=args.seed
    )
    TrainingEngine(model, training, pad_id=tokenizer.pad_id).fit(batcher, steps=args.steps)
    trained_at = time.time()

    stat = StatTest(n_bootstrap=2000, confidence_level=0.95, seed=0)
    results: dict[str, object] = {}
    per_problem: dict[int, list[float]] = {}
    for depth, problems in evaluated.items():
        prompts = [tokenizer.encode(p.prompt, add_bos=True) for p in problems]
        budget = 128 if args.arm == "explicit" else 64
        outputs = [
            tokenizer.decode(ids) for ids in generate_batch(model, prompts, budget, eos_id=tokenizer.eos_id)
        ]
        if args.arm == "explicit":
            scores = [score_explicit(p, t) for p, t in zip(problems, outputs, strict=True)]
        else:
            scores = []
            for p, t in zip(problems, outputs, strict=True):
                s = score_output(p, t)
                written = len(t.rsplit("=", 1)[0].split(",")) if s["well_formed"] else -1
                scores.append({**s, "written": written})
        exact = [1.0 if s["exact"] else 0.0 for s in scores]
        per_problem[depth] = exact
        ci = stat.compare_paired([0.0] * len(exact), exact)  # IC bootstrap de la moyenne d'exactitude
        written: dict[str, int] = {}
        for s in scores:
            written[str(s["written"])] = written.get(str(s["written"]), 0) + 1
        results[str(depth)] = {
            "n": len(problems),
            "exact": round(sum(exact) / len(exact), 4),
            "exact_ci": [ci.ci_lower, ci.ci_upper],
            "final_ok": round(sum(bool(s["final_ok"]) for s in scores) / len(scores), 4),
            "well_formed": round(sum(bool(s["well_formed"]) for s in scores) / len(scores), 4),
            "values_written": dict(sorted(written.items())),
            "passes_criterion": bool(ci.ci_lower is not None and ci.ci_lower > 0.10),
            "examples": [
                {"prompt": p.prompt, "output": t[:90], "want": document(p, args.arm)[len(p.prompt) :]}
                for p, t in list(zip(problems, outputs, strict=True))[:3]
            ],
        }
    report = {
        "arm": args.arm,
        "seed": args.seed,
        "steps": args.steps,
        "batch_size": args.batch_size,
        "tokens_seen": args.steps * args.batch_size * world.spec.context_length,
        "train_problems": len(train),
        "train_depths": sorted({p.depth for p in train}),
        "train_stream_tokens": len(stream),
        "parameters": model.num_parameters(),
        "train_seconds": round(trained_at - started),
        "total_seconds": round(time.time() - started),
        "depths": results,
        "per_problem_exact": {str(d): v for d, v in per_problem.items()},
    }
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "per_problem_exact"}
            | {
                "depths": {
                    d: {k: r[k] for k in ("exact", "exact_ci", "values_written", "passes_criterion")}
                    for d, r in results.items()
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
