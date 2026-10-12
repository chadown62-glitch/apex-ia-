"""Small real local baseline, fixed heldout cases and a deliberately faulty wheel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from apex.game_graphics.__main__ import run_pipeline
from apex.game_graphics.contract import QualityCriteria
from apex.game_graphics.controller import propose_vehicle
from apex.game_graphics.models import CoderSession, triposr_readiness
from apex.game_graphics.resources import resource_lease

CASES = (
    (
        "blue-heldout",
        "heldout",
        "Coupé bleu, RGB [0.01,0.08,0.65]. Longueur 4.2 m, largeur 1.8 m, "
        "hauteur 1.25 m, empattement 2.5 m, rayon roue 0.31 m, largeur roue 0.20 m, masse 1200 kg, "
        "metallic 0.7, roughness 0.15. Prototype reconnaissable.",
    ),
    (
        "silver-heldout",
        "heldout",
        "Coupé gris argent RGB [0.55,0.55,0.55]. Longueur 4.8 m, largeur 2.0 m, "
        "hauteur 1.4 m, empattement 2.9 m, rayon roue 0.36 m, largeur roue 0.23 m, masse 1600 kg, "
        "metallic 0.8, roughness 0.2. Prototype reconnaissable.",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.root.exists():
        raise ValueError("baseline_root_must_be_new")
    args.root.mkdir(parents=True)
    (args.root / "predeclared-cases.json").write_text(json.dumps(CASES, indent=2))
    with resource_lease(args.runtime / "resource.lock"):
        session = CoderSession(args.runtime / "coder-model.json", args.root / "model")
        generated = []
        with session:
            for family, split, prompt in CASES:
                spec, record = propose_vehicle(session.endpoint, prompt)
                session.sample_memory()
                generated.append((family, split, prompt, spec, record))
                (args.root / (family + "-controller.json")).write_text(
                    json.dumps(record, indent=2, default=str)
                )
        (args.root / "model-lifecycle.json").write_text(json.dumps(session.facts, indent=2))
        if not session.facts.get("process_stopped_verified"):
            raise RuntimeError("model_release_unverified")
        reports = []
        for family, split, prompt, spec, record in generated:
            record["lifecycle"] = session.facts
            reports.append(
                run_pipeline(
                    spec,
                    args.root / family,
                    QualityCriteria.for_level("jouable"),
                    prompt=prompt,
                    controller=record,
                    split=split,
                )
            )
        _, _, prompt, spec, record = generated[0]
        reports.append(
            run_pipeline(
                spec,
                args.root / "pivot-repair-train",
                QualityCriteria.for_level("prototype"),
                prompt=prompt + " Injected fault: wheel geometry shifted 0.12m.",
                controller=record,
                defect="pivot",
                split="heldout",
            )
        )
    result = {
        "pipelines": reports,
        "triposr": triposr_readiness(),
        "training_performed": False,
        "notes": "Heldout families and their derived faulty versions excluded from training.",
    }
    (args.root / "benchmark.json").write_text(json.dumps(result, indent=2, default=str))
    print(
        json.dumps({"results": [{"status": r["status"], "reason": r["reason"]} for r in reports]}, indent=2)
    )
    return 0 if all(r["status"] == "pass" for r in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
