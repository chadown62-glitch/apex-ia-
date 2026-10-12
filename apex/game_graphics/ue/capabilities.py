"""Observe actual UE files and hardware; declarations are never engine qualification."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

import psutil


def probe(engine_root: Path | None = None) -> dict[str, Any]:
    roots = [engine_root] if engine_root else []
    if os.environ.get("APEX_UE_ROOT"):
        roots.append(Path(os.environ["APEX_UE_ROOT"]))
    roots += [
        Path(p)
        for p in (
            "/opt/UnrealEngine",
            "/opt/unreal-engine",
            "/workspace/UnrealEngine",
            "/usr/local/UnrealEngine",
        )
    ]
    if os.name == "nt":
        roots += list(Path("C:/Program Files/Epic Games").glob("UE_5.*"))
    executable = "UnrealEditor.exe" if os.name == "nt" else "UnrealEditor"
    editor = shutil.which(executable)
    found_root = None
    for root in roots:
        candidate = root / "Engine/Binaries" / ("Win64" if os.name == "nt" else "Linux") / executable
        if candidate.is_file():
            editor, found_root = str(candidate.resolve()), root.resolve()
            break
    if editor and found_root is None:
        found_root = Path(editor).resolve().parents[3]
    checks = {}
    for name, relative in {
        "python_plugin": "Engine/Plugins/Experimental/PythonScriptPlugin/PythonScriptPlugin.uplugin",
        "editor_scripting": (
            "Engine/Plugins/Editor/EditorScriptingUtilities/EditorScriptingUtilities.uplugin"
        ),
        "pcg_plugin": "Engine/Plugins/Experimental/PCG/PCG.uplugin",
        "modeling_plugin": "Engine/Plugins/Runtime/MeshModelingToolset/MeshModelingToolset.uplugin",
    }.items():
        path = found_root / relative if found_root else None
        checks[name] = {"path": str(path) if path else None, "exists": bool(path and path.is_file())}
    version = None
    if found_root and (found_root / "Engine/Build/Build.version").is_file():
        version = json.loads((found_root / "Engine/Build/Build.version").read_text())
    available = bool(
        editor
        and os.access(editor, os.X_OK)
        and checks["python_plugin"]["exists"]
        and checks["editor_scripting"]["exists"]
    )
    feature_matrix = {
        name: {"unreal": False, "blender": False, "hybrid": False}
        for name in (
            "vehicle",
            "mesh",
            "uv_retopology",
            "environment",
            "lighting",
            "materials",
            "landscape",
            "pcg",
            "modeling_tools",
        )
    }
    feature_matrix["vehicle"]["blender"] = bool(shutil.which("blender"))
    return {
        "unreal": {
            "available": available,
            "editor": editor,
            "root": str(found_root) if found_root else None,
            "version": version,
            "checks": checks,
            "engine_executed": False,
            "qualified": False,
        },
        "blender": {"available": bool(shutil.which("blender")), "scope": "PR12 procedural coupe only"},
        "features": feature_matrix,
        "platform": platform.platform(),
        "ram_total_bytes": psutil.virtual_memory().total,
        "gpu_measurement": "unknown",
        "costs": "unknown until representative measured engine runs",
        "searched_roots": [{"path": str(r), "exists": r.is_dir()} for r in roots],
        "engine_path_lookups": {
            name: shutil.which(name)
            for name in ("UnrealEditor", "UnrealEditor-Cmd", "UnrealEditor.exe", "RunUAT.sh")
        },
        "limitations": "Plugin files do not qualify operations, PCG, landscapes or Nanite/Lumen.",
    }
