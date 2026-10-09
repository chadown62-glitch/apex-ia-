"""Exporte une version PROMUE du modèle APEX vers les poids servis par l'application (`brain/<nom>/`).

    .venv/bin/python scripts/apex_brain_export.py --checkpoint /tmp/apex-exp/v1.pt \
        --name experiment-002:v1 --out brain/apex-brain-v1 --reproduction "texte libre"

Refuse d'exporter si la version n'est pas promue dans `docs/neural/registry.jsonl`, ou si l'empreinte des
poids du checkpoint diffère de celle que le registre a mesurée pour elle : seuls les poids jugés et promus
deviennent le Cerveau APEX de l'application. Les checkpoints d'entraînement ne sont jamais versionnés ; ils se
reconstruisent à l'identique (`scripts/apex_neural_experiment.py`, voir docs/NATIVE_INTELLIGENCE.md)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from apex.brain.artifact import export  # noqa: E402
from apex.brain.model import model_fingerprint  # noqa: E402
from apex.neural.checkpoint import load_checkpoint  # noqa: E402

LIMITATIONS = [
    "pré-entraîné à prédire la suite de textes techniques seulement (docstrings de la bibliothèque standard "
    "Python, documentation d'APEX) ; jamais entraîné à suivre des consignes ni à converser",
    "1,9 M paramètres, contexte de 128 jetons : il continue le texte reçu ; sa sortie contient beaucoup de mots "
    "inventés",
    "mesuré : prédiction de texte technique jamais vu (bits par octet) ; NON mesuré : raisonnement, code, "
    "compréhension, utilité d'une réponse",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--name", required=True, help="nom de la version dans le registre (ex. experiment-002:v1)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=REPO / "docs" / "neural" / "registry.jsonl")
    parser.add_argument("--reproduction", default="", help="comment ce checkpoint a été obtenu")
    args = parser.parse_args()

    entries = [json.loads(line) for line in args.registry.read_text(encoding="utf-8").splitlines() if line.strip()]
    entry = next((e for e in entries if e.get("name") == args.name), None)
    if entry is None or not entry.get("promoted"):
        print(f"refusé : « {args.name} » n'est pas une version promue du registre", file=sys.stderr)
        return 2
    expected = entry["reports"]["heldout"]["model_fingerprint"]
    engine, tokenizer, info = load_checkpoint(args.checkpoint)
    actual = model_fingerprint(engine.model)
    if actual != expected or info.dataset_fingerprint != entry["dataset_fingerprint"]:
        print(f"refusé : poids {actual} / dataset {info.dataset_fingerprint} ; le registre a mesuré {expected} / "
              f"{entry['dataset_fingerprint']} pour « {args.name} »", file=sys.stderr)
        return 3
    reports = {name: {k: v for k, v in report.items() if k != "scores"} for name, report in entry["reports"].items()}
    experiment = args.name.split(":")[0]
    report_path = REPO / "docs" / "neural" / f"{experiment}.json"
    sample = None
    if report_path.is_file():
        sample = json.loads(report_path.read_text(encoding="utf-8")).get("samples_illustration_only", {}).get("v1")
    provenance = {
        "name": args.name,
        "description": "Cerveau APEX : ApexLM, le réseau de neurones propre à APEX (tokenizer BPE d'APEX, aucun "
                       "poids externe)",
        "registry": {"file": "docs/neural/registry.jsonl", "index": entry["index"], "hash": entry["hash"],
                     "decision": entry["decision"], "promoted": entry["promoted"], "at": entry["at"]},
        "experiment_report": f"docs/neural/{experiment}.json",
        "dataset_fingerprint": info.dataset_fingerprint,
        "tokenizer_fingerprint": tokenizer.fingerprint(),
        "training": {"step": info.step, "seed": info.seed, "software": info.software},
        "measures": reports,
        "reproduction": args.reproduction,
        "limitations": LIMITATIONS,
        "sample_illustration_only": sample,
    }
    written = export(engine.model, tokenizer, args.out, provenance)
    print(json.dumps({k: written[k] for k in ("name", "weights_sha256", "model_fingerprint", "parameters")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
