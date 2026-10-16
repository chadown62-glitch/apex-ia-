"""Build a versioned Graphics→Engineer bundle from actual Blender files."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from apex.game_engineer.assets import decode_glb, inspect_bundle
from apex.game_engineer.contract import WHEELS
from apex.tools.workspace import Workspace, WorkspaceLimits

SCHEMA = Path(__file__).resolve().parents[2] / "docs/game_graphics/vehicle-asset-v1.schema.json"
VIEWS = ("front_three_quarter.png", "rear_three_quarter.png", "side.png", "front.png")


def _legacy_expected() -> dict[str, Any]:
    centers = ((1.4, -0.9, 0.355), (1.4, 0.9, 0.355), (-1.4, -0.9, 0.355), (-1.4, 0.9, 0.355))
    return {
        "wheels": [
            {
                "object": name,
                "center_m": list(center),
                "radius_m": 0.355,
                "spin_axis_local": [0, 0, 1],
                "spin_axis_blender_world": [0, 1, 0],
            }
            for name, center in zip(WHEELS, centers, strict=True)
        ],
        "spec": {
            "length_m": 4.8,
            "width_m": 2.0,
            "height_m": 1.3,
            "wheelbase_m": 2.8,
            "wheel_radius_m": 0.355,
            "wheel_width_m": 0.32,
            "mass_kg": 1200,
            "body_color": [0.018, 0.105, 0.145],
            "paint_metallic": 0.78,
            "paint_roughness": 0.24,
        },
        "measurement_scope": "legacy expected contract, unverified against the actual GLB",
    }


def prepare_bundle(revision: Path, bundle: Path, *, source_hash: str) -> dict[str, Any]:
    """Preserve files and an independent Engineer verdict, including on failure."""
    from apex.game_engineer.contract import isolated_output

    isolated_output(bundle)
    bundle.mkdir(parents=True, exist_ok=False)
    files = {
        "vehicle.blend": revision / "asset.blend",
        "vehicle.glb": revision / "asset.glb",
        **{name: revision / name for name in VIEWS},
    }
    for name, src in files.items():
        shutil.copyfile(src, bundle / name)
    shutil.copyfile(SCHEMA, bundle / "vehicle-asset-v1.schema.json")
    metadata_file = revision / "vehicle-contract.json"
    metadata = json.loads(metadata_file.read_text()) if metadata_file.is_file() else _legacy_expected()
    if metadata_file.is_file():
        shutil.copyfile(metadata_file, bundle / "vehicle-contract.json")
    (bundle / "spec.json").write_text(json.dumps(metadata["spec"], indent=2))
    entries = []
    for path in sorted(bundle.iterdir()):
        if path.is_file():
            data = path.read_bytes()
            entries.append(
                {
                    "path": path.name,
                    "role": "source_or_contract",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
    manifest = {
        "schema_version": 1,
        "contract": "VehicleAssetContract",
        "producer": "apex.game_studio",
        "geometry_method": "procedural_blender_with_bounded_contract_repair",
        "spec": metadata["spec"],
        "units": "m",
        "blender_axes": {"forward": "+X", "up": "+Z", "handedness": "right"},
        "glb_axes": {"up": "+Y", "handedness": "right", "exporter_converted": True},
        "unreal_target": {
            "units": "cm",
            "up": "+Z",
            "forward": "+X",
            "scale_conversion": 100,
            "import_tested": False,
        },
        "wheels": metadata["wheels"],
        "files": entries,
        "rig": {"kind": "separate_static_meshes", "skeletal_rig": False, "physics_asset": False},
        "lods": [],
        "texture_maps": [],
        "source_artifact": source_hash,
        "measurement_scope": metadata["measurement_scope"],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2))
    workspace = Workspace(
        bundle.parent,
        workspace_id="game-engineer-intake",
        limits=WorkspaceLimits(max_file_bytes=25_000_000, max_total_bytes=80_000_000),
    )
    try:
        report = inspect_bundle(workspace, bundle.name)
        nodes = {
            node.get("name"): node for node in decode_glb((bundle / "vehicle.glb").read_bytes())["nodes"]
        }
        wheel_groups = {}
        for name in WHEELS:
            node = nodes.get(name) or {}
            if (node.get("extras") or {}).get("wheel_slot") != name:
                raise ValueError("wheel_export_metadata_missing:" + name)
            children = node.get("children") or []
            if len(children) < 3:
                raise ValueError("wheel_visual_components_not_grouped:" + name)
            wheel_groups[name] = len(children)
        report["wheel_metadata_verified_in_glb"] = True
        report["wheel_group_child_nodes"] = wheel_groups
        report["verdict"] = "pass_preflight"
    except Exception as exc:
        report = {
            "verdict": "fail_preflight",
            "error": type(exc).__name__ + ": " + str(exc),
            "source_artifact": source_hash,
            "unreal_import_verified": False,
        }
    (bundle / "engineer-report.json").write_text(json.dumps(report, indent=2))
    return report
