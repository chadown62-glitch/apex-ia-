"""Existing ToolEngine intake plugin plus real engine/toolchain discovery."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

import psutil
from jsonschema import ValidationError

from apex.game_engineer.assets import inspect_bundle
from apex.tools.engine import ToolContext, ToolEngine, ToolRegistry
from apex.tools.model import ErrorCode, Permission, ToolFailure, ToolSpec


def intake_engine() -> ToolEngine:
    registry = ToolRegistry()
    spec = ToolSpec(
        name="game_engineer.inspect_asset",
        description="Verify local VehicleAssetContract and GLB",
        input_schema={
            "type": "object",
            "properties": {"folder": {"type": "string"}},
            "required": ["folder"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        permissions=frozenset({Permission.READ_WORKSPACE}),
    )

    def handler(arguments: dict[str, Any], context: ToolContext, spec: ToolSpec) -> dict[str, Any]:
        try:
            return inspect_bundle(context.workspace, arguments["folder"])
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            raise ToolFailure(ErrorCode.INVALID_INPUT, str(exc)) from exc

    registry.register(spec, handler)
    return ToolEngine(registry)


def discover_engine(root: Path | None = None) -> dict[str, Any]:
    roots = [root] if root else []
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
    suffix = "Win64" if os.name == "nt" else "Linux"
    editor_name = "UnrealEditor-Cmd.exe" if os.name == "nt" else "UnrealEditor-Cmd"
    batch = "bat" if os.name == "nt" else "sh"
    probes = [{"root": str(p), "exists": p.is_dir()} for p in roots if p]
    editor = shutil.which(editor_name)
    engine_root = None
    for p in roots:
        if p is not None and (p / f"Engine/Binaries/{suffix}/{editor_name}").is_file():
            editor, engine_root = str(p / f"Engine/Binaries/{suffix}/{editor_name}"), p
            break
    if editor and engine_root is None:
        engine_root = Path(editor).resolve().parents[3]
    checks = {}
    for name, relative in {
        "build": f"Engine/Build/BatchFiles/Build.{batch}",
        "uat": f"Engine/Build/BatchFiles/RunUAT.{batch}",
        "chaos": "Engine/Plugins/Experimental/ChaosVehiclesPlugin/ChaosVehiclesPlugin.uplugin",
        "python": "Engine/Plugins/Experimental/PythonScriptPlugin/PythonScriptPlugin.uplugin",
    }.items():
        path = engine_root / relative if engine_root else None
        checks[name] = {"path": str(path) if path else None, "exists": bool(path and path.is_file())}
    version = None
    if engine_root and (engine_root / "Engine/Build/Build.version").is_file():
        version = json.loads((engine_root / "Engine/Build/Build.version").read_text())
    available = bool(editor and os.access(editor, os.X_OK) and all(c["exists"] for c in checks.values()))
    return {
        "status": "available_prerequisites_unqualified" if available else "blocked",
        "editor": editor,
        "engine_root": str(engine_root) if engine_root else None,
        "version": version,
        "checks": checks,
        "path_lookups": {
            n: shutil.which(n)
            for n in ("UnrealEditor", "UnrealEditor-Cmd", "RunUAT.sh", "clang++", "g++", "dotnet")
        },
        "searched_roots": probes,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ram_total_bytes": psutil.virtual_memory().total,
        "execution_performed": False,
        "qualification": "compilation/import/driving still require actual engine runs",
        "windows_machine_accessible": os.name == "nt",
    }
