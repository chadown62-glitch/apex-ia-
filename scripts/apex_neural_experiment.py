"""Lance l'expérience d'apprentissage du modèle APEX et écrit son rapport.

    .venv/bin/python scripts/apex_neural_experiment.py --out /tmp/apex-exp \
        --report docs/neural/experiment-001.json [--steps 600] [--corrupt-steps 200]

Les checkpoints (`--out`) sont des artefacts, jamais versionnés ; seul le
rapport JSON (mesures, empreintes, verdicts) l'est."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from apex.neural.experiment import ExperimentConfig, run_experiment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="répertoire des checkpoints")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--corrupt-steps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--layers", type=int, default=4)
    parser.add_argument("--hidden", type=int, default=192)
    parser.add_argument("--heads", type=int, default=6)
    parser.add_argument("--vocab", type=int, default=768)
    parser.add_argument("--modules", type=int, default=0, help="limiter aux N premiers modules (0 = tous)")
    parser.add_argument("--docs-mode", choices=("guard", "train"), default="guard")
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--weight-decay", type=float, default=0.1)
    args = parser.parse_args()
    base = ExperimentConfig()
    config = ExperimentConfig(
        modules=base.modules[: args.modules] if args.modules else base.modules,
        guard_docs=REPO / "docs",
        train_steps=args.steps,
        corrupt_steps=args.corrupt_steps,
        seed=args.seed,
        num_layers=args.layers,
        hidden_size=args.hidden,
        num_heads=args.heads,
        vocab_size=args.vocab,
        docs_mode=args.docs_mode,
        dropout=args.dropout,
        weight_decay=args.weight_decay,
    )
    result = run_experiment(config, args.out)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"rapport : {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
