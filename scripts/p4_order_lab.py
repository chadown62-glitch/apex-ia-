"""P4 — où `dependency_order` bloque-t-il ? Mesure contrôlée sur un modèle SPÉCIALISTE.

Constat (cycle 1 de P3.5, détails par tâche du Judge) : depuis le champion P3, 6 000 pas avec 40 à
52 % de tâches d'ordre laissent l'erreur à 0,97 à 0,98, alors que les sorties sont bien formées
(100 %) et l'ORDRE est trouvé dans 50 à 60 % des cas ; ce sont les VALEURS qui manquent (la plupart
des réponses n'ont que la première étape juste). Hypothèse : calculer une étape exige de retrouver
son instruction AILLEURS dans le texte mélangé (liaison non locale), ce que le format de réponse
standard (`a3,b7,c4=4`) laisse implicite.

Bras (modèle neuf, architecture du cerveau, réservoir d'entraînement de la capacité seul, même
budget : pas, lot, fenêtre) :
- `base`     : format de réponse standard ;
- `explicit` : chaque étape recopie l'instruction exécutée avant sa valeur (`a=3>3,b=a+4>7,…=4`) :
  l'instruction à lire est d'abord COPIÉE, le calcul devient local.

Évaluation : suite held-out PROTÉGÉE du monde (300 problèmes, jamais entraînés), exactitude, ordre
trouvé, étapes justes.

CRITÈRE PRÉ-ENREGISTRÉ (fixé avant toute exécution) : un bras ACQUIERT la tâche si la borne
inférieure de l'IC bootstrap à 95 % de l'exactitude held-out est > 0,10. « Liaison non locale =
goulot » est établi si `explicit` acquiert la tâche, `base` non, et l'écart apparié
(explicit - base) exclut 0. Si `base` acquiert la tâche, l'échec du cycle 1 ne vient PAS de
l'architecture mais de l'entraînement depuis le champion (part, budget, interférence)."""

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

from apex.capabilities.dependency_order.benchmark import OrderProblem, suite_name
from apex.capabilities.dependency_order.capability import score_output
from apex.neural.config import OptimizerSpec, SchedulerSpec, TrainingConfig
from apex.neural.loop.world import World
from apex.neural.model import ApexLM, generate_batch
from apex.neural.training import TokenBatcher, TrainingEngine
from apex.stattest import StatTest

ARMS = ("base", "explicit")
_EXPLICIT = re.compile(r"((?:[a-z]=[^,>]+>-?\d+,)*[a-z]=[^,>]+>-?\d+)=(-?\d+)")


def explicit_answer(problem: OrderProblem) -> str:
    steps = problem.base.program.split(";")
    items = ",".join(f"{s}>{v}" for s, v in zip(steps, problem.base.values, strict=True))
    return f"{items}={problem.base.values[-1]}"


def score_explicit(problem: OrderProblem, text: str) -> dict[str, object]:
    match = _EXPLICIT.fullmatch(text)
    if match is None:
        return {"exact": False, "well_formed": False, "order_ok": False, "steps_ok": 0, "copied_ok": False}
    items = match.group(1).split(",")
    copied = [item.rsplit(">", 1)[0] for item in items]
    values = [int(item.rsplit(">", 1)[1]) for item in items]
    want = list(problem.base.values)
    return {
        "exact": copied == problem.base.program.split(";")
        and values == want
        and int(match.group(2)) == want[-1],
        "well_formed": True,
        "order_ok": [c.partition("=")[0] for c in copied] == list(problem.base.names),
        "steps_ok": sum(1 for got, exp in zip(values, want, strict=False) if got == exp),
        "copied_ok": copied == problem.base.program.split(";"),
    }


def document(problem: OrderProblem, arm: str) -> str:
    return problem.prompt + (explicit_answer(problem) if arm == "explicit" else problem.answer)


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
    _, bench = world.capabilities["dependency_order"]
    heldout = bench.suites[suite_name("heldout")].problems
    evaluated = {p.canonical for p in bench.eval_problems()}
    train = [p for p in bench.pool if p.canonical not in evaluated]
    order = random.Random(args.seed).sample(train, len(train))  # noqa: S311
    stream: list[int] = []
    for p in order:
        stream += tokenizer.encode(document(p, args.arm), add_bos=True, add_eos=True)

    torch.manual_seed(args.seed)
    model = ApexLM(world.spec.model_config(tokenizer.vocab_size))
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

    prompts = [tokenizer.encode(p.prompt, add_bos=True) for p in heldout]
    budget = 128 if args.arm == "explicit" else 64
    outputs = [
        tokenizer.decode(ids) for ids in generate_batch(model, prompts, budget, eos_id=tokenizer.eos_id)
    ]
    score = score_explicit if args.arm == "explicit" else score_output
    scores = [score(p, t) for p, t in zip(heldout, outputs, strict=True)]
    exact = [1.0 if s["exact"] else 0.0 for s in scores]
    ci = StatTest(n_bootstrap=2000, confidence_level=0.95, seed=0).compare_paired([0.0] * len(exact), exact)
    n = len(scores)
    report = {
        "arm": args.arm,
        "seed": args.seed,
        "steps": args.steps,
        "batch_size": args.batch_size,
        "tokens_seen": args.steps * args.batch_size * world.spec.context_length,
        "train_problems": len(train),
        "train_stream_tokens": len(stream),
        "parameters": model.num_parameters(),
        "train_seconds": round(trained_at - started),
        "heldout": {
            "n": n,
            "exact": round(sum(exact) / n, 4),
            "exact_ci": [ci.ci_lower, ci.ci_upper],
            "acquired": bool(ci.ci_lower is not None and ci.ci_lower > 0.10),
            "well_formed": round(sum(bool(s["well_formed"]) for s in scores) / n, 4),
            "order_ok": round(sum(bool(s["order_ok"]) for s in scores) / n, 4),
            "steps_ok": {str(k): sum(1 for s in scores if s["steps_ok"] == k) for k in range(6)},
            "examples": [
                {"prompt": p.prompt, "output": t[:90], "want": document(p, args.arm)[len(p.prompt) :]}
                for p, t in list(zip(heldout, outputs, strict=True))[:4]
            ],
        },
        "per_problem_exact": exact,
    }
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "per_problem_exact"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
