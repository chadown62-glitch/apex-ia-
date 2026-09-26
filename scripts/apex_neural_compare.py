"""Compare deux versions du modèle APEX issues de deux rapports d'expérience
avec le Judge (`apex.neural.evaluation.NeuralJudge`).

    .venv/bin/python scripts/apex_neural_compare.py docs/neural/experiment-002.json:v1 \
        docs/neural/experiment-003.json:v1 [--primary heldout]

La comparaison est REFUSÉE si les benchmarks diffèrent (empreinte ou
documents) : deux expériences sur des données différentes ne se comparent pas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from apex.neural.evaluation import EvalReport, NeuralJudge  # noqa: E402


def _load(spec: str) -> dict[str, EvalReport]:
    path, _, version = spec.rpartition(":")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: EvalReport.from_dict(report) for name, report in data["evaluations"][version].items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="rapport.json:version")
    parser.add_argument("candidate", help="rapport.json:version")
    parser.add_argument("--primary", default="heldout")
    args = parser.parse_args()
    verdict = NeuralJudge(primary=args.primary).judge(_load(args.baseline), _load(args.candidate))
    print(json.dumps(verdict.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
