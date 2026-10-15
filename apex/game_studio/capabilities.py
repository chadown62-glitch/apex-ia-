"""Observed dependencies and explicitly unqualified Game Studio capabilities."""

from __future__ import annotations

import os
import shutil
from importlib.util import find_spec
from pathlib import Path
from typing import Any


def coder_manifest() -> Path:
    home = Path(os.environ.get("APEX_HOME", str(Path.cwd()))).resolve()
    return Path(
        os.environ.get("APEX_GAME_STUDIO_MODEL_MANIFEST", str(home / ".apex/game-studio/coder-model.json"))
    )


def native_coder_endpoint():
    """APEX's configured local native endpoint; no second model registry."""
    try:
        from apex.config.loader import load_config
        from apex.models.provider import get_default_provider

        for endpoint in load_config().endpoints:
            if (endpoint.runtime == "transformers" and "coder" in endpoint.served_model.lower()
                    and get_default_provider(endpoint).detect().is_available):
                return endpoint
    except (OSError, ValueError, ImportError):
        pass
    return None


def probe() -> dict[str, Any]:
    scripts = Path(__file__).resolve().parents[2] / "scripts/game_graphics/studio"
    blender = shutil.which("blender")
    python_dependencies = {name: find_spec(name) is not None for name in ("psutil", "numpy", "PIL")}
    script_ready = all(
        (scripts / name).is_file() for name in ("render_asset.py", "inspect_asset.py", "build_assets.py")
    )
    manifest = coder_manifest()
    native = native_coder_endpoint()
    llama_available = find_spec("llama_cpp") is not None
    pinned = manifest.is_file() and llama_available
    ready = bool(blender and script_ready and all(python_dependencies.values()) and (pinned or native))
    home = Path(os.environ.get("APEX_HOME", str(Path.cwd()))).resolve()
    validated_here = {"vehicule": False, "batiment": False, "objet_3d": False}
    for receipt in (home / ".apex/game-studio/missions").glob("*/receipt.json"):
        try:
            import json

            data = json.loads(receipt.read_text())
            family = data.get("request", {}).get("creation_type")
            if (isinstance(family, str) and family in validated_here
                    and data.get("status") == "review" and any(
                revision.get("verdict") == "pass" for revision in data.get("revisions", [])
            )):
                validated_here[family] = True
        except (OSError, ValueError):
            continue
    try:
        from apex.game_graphics.ue.capabilities import probe as unreal_probe

        ue = unreal_probe()["unreal"]
    except ImportError:
        ue = {"available": False}
    try:
        import torch

        cuda = bool(torch.cuda.is_available())
        cuda_devices = torch.cuda.device_count() if cuda else 0
    except ImportError:
        cuda, cuda_devices = False, 0
    neural_paths = {}
    for name, variable in (
        ("source", "APEX_TRIPOSR_SOURCE"),
        ("weights", "APEX_TRIPOSR_WEIGHTS"),
        ("config", "APEX_TRIPOSR_CONFIG"),
        ("dino_config", "APEX_TRIPOSR_DINO_CONFIG"),
    ):
        value = os.environ.get(variable)
        neural_paths[name] = bool(
            value and (Path(value).is_dir() if name == "source" else Path(value).is_file())
        )
    neural_inputs_present = all(neural_paths.values())
    return {
        "vehicle_blender": {
            "status": "available_validated"
            if ready and validated_here["vehicule"]
            else "available_unqualified"
            if ready
            else "blocked_dependency",
            "blender": blender,
            "workers_present": script_ready,
            "coder_manifest_present": manifest.is_file(),
            "llama_cpp_present": llama_available,
            "apex_native_coder_present": native is not None,
            "python_dependencies": python_dependencies,
            "qualification": (
                "A saved vehicle mission on this host passed the technical judge"
                if validated_here["vehicule"]
                else "PR17 cloud Blender 4.3.2; this host awaits a successful mission"
            ),
        },
        "building_blender": {
            "status": "available_validated" if ready and validated_here["batiment"]
            else "available_unqualified" if ready
            else "blocked_dependency"
        },
        "chair_blender": {
            "status": "available_validated" if ready and validated_here["objet_3d"]
            else "available_unqualified" if ready
            else "blocked_dependency"
        },
        "unreal": {
            "status": "blocked_dependency",
            "engine_present": bool(ue["available"]),
            "engine_qualified": False,
            "reason": "UE5.8 scene execution and reopen have not been verified on this host",
        },
        "cuda": {
            "status": "available_unqualified" if cuda else "blocked_dependency",
            "available": cuda,
            "device_count": cuda_devices,
            "reason": "No TripoSR CUDA performance or memory qualification yet",
        },
        "neural_3d": {
            "status": "not_implemented" if cuda and neural_inputs_present else "blocked_dependency",
            "default_device": "cuda",
            "cpu_requires_explicit_choice": True,
            "configured_inputs_present": neural_paths,
            "required_configuration": {
                "source": "APEX_TRIPOSR_SOURCE",
                "weights": "APEX_TRIPOSR_WEIGHTS",
                "config": "APEX_TRIPOSR_CONFIG",
                "dino_config": "APEX_TRIPOSR_DINO_CONFIG",
            },
            "reason": (
                "TripoSR needs configured local inputs, actual CUDA qualification, "
                "and Game Studio mission dispatch"
            ),
        },
        "game_engineer": {
            "status": "not_implemented",
            "reason": (
                "Game Engineer contract exists, but Game Studio does not dispatch "
                "an engineer mission yet"
            ),
        },
        "environment": {"status": "not_implemented"},
        "open_world": {"status": "not_implemented"},
        "supported_styles": ["realiste"],
        "quality_certification": ["prototype"],
    }
