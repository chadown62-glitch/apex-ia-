"""Construction du Cerveau APEX v2, étape par étape (chaque étape écrit ses résultats et leur provenance).

    corpus     -> textes réels vérifiés (apex.neural.corpus)
    tokenizer  -> BPE d'APEX entraîné sur un échantillon équilibré du corpus
    tokenize   -> flux binaires train/validation (apex.neural.shards)
    tasks      -> exercices d'instruction vérifiables, train + évaluation (apex.neural.tasks,
    apex.neural.sft)
    train      -> pré-entraînement ou instruction, reprise automatique (apex.neural.pretrain)
    export     -> poids servis (apex.brain.artifact)

Exemple (modèle de validation, CPU) :

    python scripts/apex_brain_build.py corpus --out work/corpus
    python scripts/apex_brain_build.py tokenizer --corpus work/corpus --out work/tokenizer.json --vocab 8192
    python scripts/apex_brain_build.py tokenize --corpus work/corpus --tokenizer work/tokenizer.json --out
    work/data
    python scripts/apex_brain_build.py train --run work/pretrain --preset cpu-validation --data work/data
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: Préréglages d'architecture et d'optimisation. `cpu-validation` : le modèle entraîné dans l'environnement
#: de développement (4 cœurs, sans GPU). `rtx4060ti-8gb` : le grand entraînement sur le PC de l'utilisateur.
PRESETS: dict[str, dict] = {
    # Essai de la chaîne complète en quelques minutes (aucune qualité attendue).
    "tiny-test": {
        "model": {
            "hidden_size": 64,
            "num_layers": 2,
            "num_heads": 4,
            "num_kv_heads": 2,
            "intermediate_size": 128,
            "context_length": 256,
            "qk_norm": True,
            "rope_theta": 10000.0,
        },
        "train": {
            "window": 256,
            "batch_size": 4,
            "max_steps": 20,
            "lr": 3e-3,
            "warmup_steps": 2,
            "eval_every": 10,
            "eval_tokens": 5000,
        },
        "sft": {
            "replay_fraction": 0.2,
            "window": 256,
            "batch_size": 4,
            "max_steps": 10,
            "lr": 1e-3,
            "warmup_steps": 2,
            "eval_every": 5,
        },
    },
    "cpu-validation": {
        "model": {
            "hidden_size": 384,
            "num_layers": 8,
            "num_heads": 6,
            "num_kv_heads": 2,
            "intermediate_size": 1024,
            "context_length": 1024,
            "qk_norm": True,
            "rope_theta": 10000.0,
        },
        "train": {
            "window": 1024,
            "batch_size": 16,
            "grad_accumulation_steps": 1,
            "max_steps": 9000,
            "lr": 3e-3,
            "warmup_steps": 300,
            "min_lr_ratio": 0.1,
            "eval_every": 500,
            "eval_tokens": 100_000,
            "weights": {"en": 0.35, "code": 0.30, "fr": 0.25, "translation": 0.10},
        },
        "sft": {
            "replay_fraction": 0.2,
            "window": 1024,
            "batch_size": 16,
            "max_steps": 1500,
            "lr": 1e-3,
            "warmup_steps": 50,
            "min_lr_ratio": 0.1,
            "eval_every": 250,
        },
    },
    "rtx4060ti-8gb": {
        "model": {
            "hidden_size": 768,
            "num_layers": 16,
            "num_heads": 12,
            "num_kv_heads": 4,
            "intermediate_size": 2048,
            "context_length": 2048,
            "qk_norm": True,
            "rope_theta": 50000.0,
        },
        "train": {
            "window": 2048,
            "batch_size": 8,
            "grad_accumulation_steps": 8,
            # 20 000 pas * 8 * 8 * 2 048 jetons = 2,6 milliards de jetons (≈ 20 jetons par paramètre).
            "max_steps": 20000,
            "lr": 1.5e-3,
            "warmup_steps": 1000,
            "min_lr_ratio": 0.1,
            "eval_every": 1000,
            "eval_tokens": 500_000,
            "weights": {"en": 0.45, "code": 0.12, "fr": 0.40, "translation": 0.03},
        },
        "sft": {
            "replay_fraction": 0.2,
            "window": 2048,
            "batch_size": 8,
            "grad_accumulation_steps": 4,
            "max_steps": 3000,
            "lr": 3e-4,
            "warmup_steps": 100,
            "min_lr_ratio": 0.1,
            "eval_every": 250,
        },
    },
}


def cmd_corpus(args: argparse.Namespace) -> None:
    from apex.neural.brain_sources import gpu_sources, validation_sources
    from apex.neural.corpus import build_corpus

    started = time.time()
    from apex.neural.brain_sources import local_sources

    sources = {"gpu": gpu_sources, "validation": validation_sources, "local": local_sources}[args.profile](
        REPO
    )
    manifest = build_corpus(sources, Path(args.out), cache=Path(args.cache))
    print(
        f"corpus : {manifest['documents']} documents, {manifest['bytes'] / 1e6:.1f} Mo en "
        f"{time.time() - started:.0f} s"
    )


def cmd_tokenizer(args: argparse.Namespace) -> None:
    from apex.brain.tokenizer import CHAT_SPECIAL_TOKENS, ByteBPETokenizer
    from apex.neural.corpus import read_text_sample

    budgets = {k: int(float(v) * 1e6) for k, v in (item.split("=") for item in args.budget_mb.split(","))}
    started = time.time()
    texts = read_text_sample(Path(args.corpus), budgets)
    print(f"échantillon : {len(texts)} documents, {sum(len(t) for t in texts) / 1e6:.1f} M caractères")
    tokenizer = ByteBPETokenizer.train(texts, args.vocab, special_tokens=CHAT_SPECIAL_TOKENS, pattern="v2")
    tokenizer.save(args.out)
    sample = "\n".join(texts[:: max(1, len(texts) // 200)])[:2_000_000]
    ratio = len(sample.encode("utf-8")) / max(1, len(tokenizer.encode(sample)))
    print(
        f"tokenizer : {tokenizer.vocab_size} jetons, {ratio:.2f} octets/jeton, "
        f"{time.time() - started:.0f} s, "
        f"empreinte {tokenizer.fingerprint()[:16]}"
    )


def cmd_tokenize(args: argparse.Namespace) -> None:
    from apex.brain.tokenizer import ByteBPETokenizer
    from apex.neural.shards import tokenize_corpus

    tokenizer = ByteBPETokenizer.load(args.tokenizer)
    out = Path(args.out)
    started = time.time()
    manifest = tokenize_corpus(
        Path(args.corpus),
        tokenizer,
        out,
        workers=args.workers,
        validation_per_mille=args.validation_per_mille,
    )
    shutil.copy(args.tokenizer, out / "tokenizer.json")
    total = sum(s["tokens"] for s in manifest["streams"].values())
    print(f"tokenisation : {total / 1e6:.1f} M jetons en {time.time() - started:.0f} s")
    print(json.dumps(manifest["streams"], indent=1))


def cmd_tasks(args: argparse.Namespace) -> None:
    from apex.brain.tokenizer import ByteBPETokenizer
    from apex.neural.sft import prepare_sft
    from apex.neural.tasks import eval_set, generate

    tokenizer = ByteBPETokenizer.load(args.tokenizer)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evaluation = eval_set()
    keys = frozenset(e.key() for e in evaluation)
    with (out / "eval.jsonl").open("w", encoding="utf-8") as handle:
        for example in evaluation:
            handle.write(json.dumps(example.to_row(), ensure_ascii=False) + "\n")
    train = [e.to_row() for e in generate(args.train, seed=args.seed, exclude=keys)]
    validation = [e.to_row() for e in generate(args.validation, seed=args.seed + 1, exclude=keys)]
    manifest = prepare_sft({"train": train, "validation": validation}, tokenizer, out, window=args.window)
    print(
        json.dumps(
            {
                k: {kk: vv for kk, vv in v.items() if kk != "families"}
                for k, v in manifest["splits"].items()
            },
            indent=1,
        )
    )


def _run_config(args: argparse.Namespace, stage: str):
    from apex.brain.tokenizer import ByteBPETokenizer
    from apex.neural.pretrain import RunConfig

    preset = PRESETS[args.preset]
    tokenizer = ByteBPETokenizer.load(Path(args.data) / "tokenizer.json")
    model = {**preset["model"], "vocab_size": tokenizer.vocab_size, "pad_token_id": tokenizer.pad_id}
    train = dict(preset["train" if stage == "pretrain" else "sft"])
    if args.max_steps:
        train["max_steps"] = args.max_steps
    if stage == "sft" and getattr(args, "replay_data", None):
        train["replay_data_dir"] = str(Path(args.replay_data).resolve())
        train["weights"] = preset["train"].get("weights", {})
    elif stage == "sft":
        train.pop("replay_fraction", None)
    return RunConfig(
        model=model,
        data_dir=str(Path(args.data).resolve()),
        stage=stage,
        threads=args.threads,
        device=args.device,
        init_from=args.init_from,
        **train,
    )


def cmd_train(args: argparse.Namespace) -> None:
    from apex.neural.pretrain import Run

    run_dir = Path(args.run)
    config = None if (run_dir / "run.json").is_file() else _run_config(args, args.stage)
    run = Run(run_dir, config)
    result = run.train(max_hours=args.max_hours, stop_after_steps=args.steps)
    print(json.dumps(result))


def cmd_export(args: argparse.Namespace) -> None:
    import hashlib

    from apex.brain.tokenizer import ByteBPETokenizer
    from apex.neural.pretrain import export_weights, run_summary

    tokenizer = ByteBPETokenizer.load(args.tokenizer)
    provenance: dict = {
        "name": Path(args.out).name,
        "description": (
            "Cerveau APEX v2 : ApexLM, modèle de langage construit et entraîné de zéro par APEX "
            "(architecture, tokenizer, données et entraînement propres ; aucun poids externe)"
        ),
        "generation": 2,
        "tokenizer_fingerprint": tokenizer.fingerprint(),
        "training": {
            "pretrain": run_summary(Path(args.run_pretrain)) if args.run_pretrain else None,
            "instruction": run_summary(Path(args.run)),
        },
    }
    if args.data:
        manifest = Path(args.data) / "manifest.json"
        provenance["data"] = {
            "tokens_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "corpus_manifest_sha256": json.loads(manifest.read_text())["corpus_manifest_sha256"],
        }
    if args.provenance:
        provenance.update(json.loads(Path(args.provenance).read_text(encoding="utf-8")))
    written = export_weights(
        Path(args.run),
        tokenizer,
        Path(args.out),
        provenance,
        quantization=None if args.quantize == "none" else args.quantize,
    )
    print(
        json.dumps(
            {
                k: written.get(k)
                for k in (
                    "weights_sha256",
                    "model_fingerprint",
                    "parameters",
                    "quantization",
                    "quantization_report",
                )
            }
        )
    )


def cmd_evaluate(args: argparse.Namespace) -> None:
    """Rapport complet du Cerveau servi (qualité, vitesse, mémoire, stabilité) ; écrit `evaluation.json` à
    côté des poids et en reporte le résumé dans la provenance (les poids eux-mêmes ne changent pas)."""
    import torch

    from apex.brain.artifact import inspect
    from apex.neural.brain_eval import full_report, load_rows

    brain = Path(args.brain)
    rows = load_rows(Path(args.tasks))
    if args.limit:
        rows = rows[: args.limit]
    fp32 = None
    if args.checkpoint:
        fp32 = torch.load(args.checkpoint, map_location="cpu", weights_only=False)["engine"]["model"]
    report = full_report(
        brain,
        rows,
        data_dir=Path(args.data) if args.data else None,
        fp32_state=fp32,
        stability_count=args.stability,
    )
    (brain / "evaluation.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = {
        "measured_at": report["measured_at"],
        "tasks_rate": report["tasks"]["rate"],
        "tasks": {k: v["rate"] for k, v in report["tasks"]["families"].items()},
        "decode_tokens_per_s_cpu": [r["decode_tokens_per_s"] for r in report["speed"]["runs"]],
        "time_to_first_token_s_cpu": [r["time_to_first_token_s"] for r in report["speed"]["runs"]],
        "stability_errors": report["stability"]["errors"],
        "bpb_served": (report.get("bpb_served") or {}).get("all"),
        "bpb_float32": (report.get("bpb_float32") or {}).get("all"),
    }
    provenance_file = brain / "provenance.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    provenance["evaluation"] = summary
    provenance_file.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not inspect(brain).ok:
        raise SystemExit("provenance réécrite mais Cerveau incohérent : ne pas installer")
    print(json.dumps(summary, indent=1, ensure_ascii=False))


def cmd_install(args: argparse.Namespace) -> None:
    """Copie un Cerveau vérifié dans un dossier de Cerveaux d'APEX (ex. `%LOCALAPPDATA%\\APEX\\brain`) ;
    l'ancien est conservé sous `<nom>.previous` (retour arrière possible)."""
    from apex.brain.artifact import inspect

    source = Path(args.brain)
    found = inspect(source)
    if not found.ok:
        raise SystemExit(f"Cerveau refusé : {found.detail}")
    target = Path(args.to) / (args.name or source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".installing")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging)
    if not inspect(staging).ok:
        shutil.rmtree(staging, ignore_errors=True)
        raise SystemExit("copie incohérente : installation annulée")
    previous = target.with_name(target.name + ".previous")
    if target.exists():
        shutil.rmtree(previous, ignore_errors=True)
        target.rename(previous)
    staging.rename(target)
    print(
        f"Cerveau installé : {target}"
        + (f" (précédent conservé : {previous})" if previous.exists() else "")
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("corpus")
    p.add_argument("--out", required=True)
    p.add_argument("--cache", default=str(REPO / ".apex" / "corpus-cache"))
    p.add_argument(
        "--profile",
        choices=("validation", "gpu", "local"),
        default="validation",
        help="gpu : ajoute les textes publics du Hub Hugging Face (grand entraînement) ; local : fichiers du poste",
    )
    p.set_defaults(func=cmd_corpus)
    p = sub.add_parser("tokenizer")
    p.add_argument("--corpus", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--vocab", type=int, default=8192)
    p.add_argument("--budget-mb", default="en=25,fr=15,code=25,translation=5")
    p.set_defaults(func=cmd_tokenizer)
    p = sub.add_parser("tokenize")
    p.add_argument("--corpus", required=True)
    p.add_argument("--tokenizer", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--validation-per-mille", type=int, default=20)
    p.set_defaults(func=cmd_tokenize)
    p = sub.add_parser("tasks")
    p.add_argument("--tokenizer", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--train", type=int, default=200_000)
    p.add_argument("--validation", type=int, default=2000)
    p.add_argument("--window", type=int, default=1024)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=cmd_tasks)
    p = sub.add_parser("train")
    p.add_argument("--run", required=True)
    p.add_argument("--stage", choices=("pretrain", "sft"), default="pretrain")
    p.add_argument("--preset", choices=sorted(PRESETS), default="cpu-validation")
    p.add_argument("--data", help="données tokenisées (pretrain) ou d'instruction (sft)")
    p.add_argument("--init-from", help="checkpoint de départ (instruction après pré-entraînement)")
    p.add_argument("--replay-data", help="instruction : corpus tokenisé rejoué (protection contre l'oubli)")
    p.add_argument("--max-steps", type=int, default=0)
    p.add_argument("--max-hours", type=float)
    p.add_argument("--steps", type=int, help="s'arrêter après N étapes (reprise possible)")
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--device", default="auto")
    p.set_defaults(func=cmd_train)
    p = sub.add_parser("export")
    p.add_argument("--run", required=True, help="exécution dont le checkpoint est exporté (instruction)")
    p.add_argument("--run-pretrain", help="exécution de pré-entraînement (résumé dans la provenance)")
    p.add_argument("--data", help="données tokenisées du pré-entraînement (empreintes dans la provenance)")
    p.add_argument("--tokenizer", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--quantize", choices=("apex-q5", "none"), default="apex-q5")
    p.add_argument("--provenance", help="JSON ajouté à la provenance")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("evaluate")
    p.add_argument("--brain", required=True)
    p.add_argument("--tasks", required=True, help="eval.jsonl écrit par la commande tasks")
    p.add_argument("--data", help="données tokenisées (bits par octet sur la validation)")
    p.add_argument("--checkpoint", help="checkpoint pleine précision (coût mesuré de la quantification)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--stability", type=int, default=100)
    p.set_defaults(func=cmd_evaluate)
    p = sub.add_parser("install")
    p.add_argument("--brain", required=True)
    p.add_argument("--to", required=True, help="dossier des Cerveaux d'APEX")
    p.add_argument("--name", help="nom installé (défaut : celui du dossier)")
    p.set_defaults(func=cmd_install)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
