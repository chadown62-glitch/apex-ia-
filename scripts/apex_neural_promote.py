"""Propose la version d'un rapport d'expérience au registre des versions du
modèle APEX ; elle n'est promue que sur ACCEPT du Judge.

    .venv/bin/python scripts/apex_neural_promote.py docs/neural/experiment-002.json \
        --registry docs/neural/registry.jsonl [--version v1] [--exclude guard_apex_docs]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from apex.neural.registry import ModelRegistry, proposal_from_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--version", default="v1")
    parser.add_argument("--exclude", action="append", default=[], help="suite à ne pas comparer")
    args = parser.parse_args()
    entry = ModelRegistry(args.registry).propose(
        proposal_from_report(args.report, args.version, exclude=tuple(args.exclude))
    )
    print(
        json.dumps(
            {k: entry[k] for k in ("index", "name", "decision", "promoted", "reasons")},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
