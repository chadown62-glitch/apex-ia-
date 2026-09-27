"""Échelle de TRANSFERT : mesure des champions de plusieurs boucles sur le même monde.

Barreaux, du plus proche au plus lointain de l'entraînement de `program_trace` :
1. domaine entraîné      : suite `program_trace:train` (solutions vues) ;
2. même forme, held-out  : suite `program_trace:heldout` (programmes jamais vus) ;
3. forme NOUVELLE        : mêmes programmes held-out, question sur une variable INTERMÉDIAIRE
   (`?b:` au lieu de la dernière) ; attendu : la trace jusqu'à `b`, puis sa valeur ;
4. tâche APPARENTÉE      : `dependency_order:heldout` (instructions mélangées) ;
5. tâche LOINTAINE       : `code_repair:heldout`.

Chaque modèle est le champion attesté (dernière version promue) du registre de sa boucle, rechargé
avec les contrôles de filiation et de tokenizer du monde. Écarts entre modèles : bootstrap apparié
par problème. Aucun barreau n'autorise le mot « raisonnement général »."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.capabilities.dependency_order.capability import score_output as order_score
from apex.capabilities.program_trace.benchmark import suite_name as trace_suite
from apex.capabilities.program_trace.capability import score_output as trace_score
from apex.capabilities.program_trace.generator import Problem, interpret
from apex.capabilities.registry import sandbox_provider
from apex.neural.loop.execute import load_model_from_artifact
from apex.neural.loop.world import World
from apex.neural.model import ApexLM, generate_batch
from apex.neural.registry import ModelRegistry
from apex.stattest import StatTest


def intermediate(problem: Problem) -> tuple[str, Problem]:
    """Même programme, question sur l'avant-dernière variable ; trace attendue jusqu'à elle."""
    asked = problem.depth - 1
    steps = problem.program.split(";")[: asked + 1]
    return f"{problem.program}?{problem.names[asked]}:", interpret(";".join(steps))


def generate(model: ApexLM, world: World, prompts: list[str]) -> list[str]:
    ids = [world.tokenizer.encode(p, add_bos=True) for p in prompts]
    return [
        world.tokenizer.decode(o) for o in generate_batch(model, ids, 48, eos_id=world.tokenizer.eos_id)
    ]


def rungs(model: ApexLM, world: World) -> dict[str, list[dict[str, object]]]:
    _, trace = world.capabilities["program_trace"]
    out: dict[str, list[dict[str, object]]] = {}
    for rung, split in (("1_train_domain", "train"), ("2_same_form_heldout", "heldout")):
        problems = trace.suites[trace_suite(split)].problems
        texts = generate(model, world, [p.prompt for p in problems])
        out[rung] = [trace_score(p, t) for p, t in zip(problems, texts, strict=True)]
    problems = trace.suites[trace_suite("heldout")].problems
    asked = [intermediate(p) for p in problems]
    texts = generate(model, world, [prompt for prompt, _ in asked])
    out["3_new_form_intermediate"] = [
        {**trace_score(want, t), "wrote_full_program": t.count(",") == p.depth}
        for (_, want), p, t in zip(asked, problems, texts, strict=True)
    ]
    if "dependency_order" in world.capabilities:
        from apex.capabilities.dependency_order.benchmark import suite_name as order_suite

        _, order = world.capabilities["dependency_order"]
        problems_o = order.suites[order_suite("heldout")].problems
        texts = generate(model, world, [p.prompt for p in problems_o])
        out["4_related_dependency_order"] = [
            order_score(p, t) for p, t in zip(problems_o, texts, strict=True)
        ]
    capability, data = world.capabilities["code_repair"]
    evaluation = capability.evaluate(model, world.tokenizer, data, provider=sandbox_provider())
    out["5_distant_code_repair"] = [
        {"exact": bool(r["solved"])} for r in evaluation.details["cap:code_repair:heldout"]
    ]
    return out


def champion(workdir: Path, world: World) -> tuple[str, ApexLM]:
    current = ModelRegistry(workdir / "registry.jsonl", primary="heldout").current()
    if current is None:
        raise SystemExit(f"{workdir} : aucun champion promu")
    store = LocalFilesystemArtifactStore(root=str(workdir / "artifacts"))
    return str(current["name"]), load_model_from_artifact(store, current["artifact"], world)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True, help="étiquette=workdir de la boucle")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    world = World.load(args.world)
    stat = StatTest(n_bootstrap=2000, confidence_level=0.95, seed=0)
    exact: dict[str, dict[str, list[float]]] = {}
    report: dict[str, object] = {"world": world.fingerprint, "models": {}, "pairs": {}}
    for spec in args.model:
        label, _, workdir = spec.partition("=")
        name, model = champion(Path(workdir), world)
        rows = rungs(model, world)
        exact[label] = {r: [1.0 if row["exact"] else 0.0 for row in v] for r, v in rows.items()}
        summary = {}
        for rung, values in rows.items():
            keys = sorted({k for row in values for k, v in row.items() if isinstance(v, bool)})
            summary[rung] = {"n": len(values)} | {
                k: round(sum(bool(row.get(k)) for row in values) / len(values), 4) for k in keys
            }
        report["models"][label] = {"champion": name, "workdir": workdir, "rungs": summary}  # type: ignore[index]
    labels = list(exact)
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            for rung in exact[a]:
                gain = stat.compare_paired(exact[b][rung], exact[a][rung])  # > 0 : `a` meilleur
                report["pairs"][f"{a} - {b} :: {rung}"] = {  # type: ignore[index]
                    "diff": round(
                        sum(exact[a][rung]) / len(exact[a][rung])
                        - sum(exact[b][rung]) / len(exact[b][rung]),
                        4,
                    ),
                    "ci": [gain.ci_lower, gain.ci_upper],
                    "verdict": gain.verdict.value,
                }
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
