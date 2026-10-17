"""Reproduce the measured ground-context comparison using versioned PR24 assets.

This is trusted Blender assembly, NOT a fresh Qwen plan or neuronal generation.
Run from the repository with PYTHONPATH=. and a new output directory.
See REPORT.md for the complete command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from apex.game_engineer.assets import decode_glb
from apex.game_graphics.studio.execution import refuse_personal_project, run_blender
from apex.sandbox.model import ExecutionStatus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    refuse_personal_project(output)
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[3]
    assets = []
    for key, family, offset in (
        ("building", "building", [0, 3.2, 0]),
        ("chair_left", "chair", [-2, -1, 0]),
        ("chair_right", "chair", [2, -1, 0]),
    ):
        path = root / f"docs/game_studio/mission23/evidence/assets/{family}/asset.glb"
        assets.append(
            {
                "key": key,
                "path": str(path),
                "offset_m": offset,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    inputs = output / "inputs.json"
    inputs.write_text(json.dumps(assets, indent=2))
    for setting in ("street", "courtyard"):
        target = output / setting
        for phase, arguments in (("build", ["--assets-manifest", str(inputs)]), ("reopen", ["--verify"])):
            result = run_blender(
                "assemble_garage.py",
                ["--output", str(target), "--setting", setting, *arguments],
                output / f"{setting}-{phase}.log",
                timeout_s=300,
                rss_limit_mib=6000,
            )
            (output / f"{setting}-{phase}-execution.json").write_text(
                json.dumps(result.to_dict(), indent=2)
            )
            if result.status is not ExecutionStatus.SUCCESS:
                raise RuntimeError(f"{setting}/{phase}: {result.error_detail}")
        gltf = decode_glb((target / "garage.glb").read_bytes())
        names = [node.get("name", "") for node in gltf["nodes"]]
        report = {
            "setting": setting,
            "road_objects": [n for n in names if n.startswith("street")],
            "courtyard_floor_present": "courtyard_floor" in names,
            "views": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.glob("*.png")},
        }
        if setting == "courtyard" and (report["road_objects"] or not report["courtyard_floor_present"]):
            raise RuntimeError("exported_ground_context_mismatch")
        (target / "independent-glb-check.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
