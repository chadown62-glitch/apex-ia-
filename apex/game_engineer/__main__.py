"""Stage a real Graphics bundle in a fresh isolated root and report engine blockers."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from apex.artifacts.local_filesystem import LocalFilesystemArtifactStore
from apex.config.schema import RecoveryPolicy
from apex.game_engineer.contract import EngineCriteria, isolated_output
from apex.game_engineer.judge import UnrealTechnicalOracle
from apex.game_engineer.tools import discover_engine, intake_engine
from apex.oracles.model import OracleVerdict
from apex.recovery.model import FailureCategory
from apex.recovery.policy import decide
from apex.tools.engine import ToolContext
from apex.tools.model import Permission, ToolCall
from apex.tools.workspace import Workspace, WorkspaceLimits

LIMITS = WorkspaceLimits(max_file_bytes=16_000_000, max_total_bytes=64_000_000, max_files=64)


def prepare(
    bundle: Path, root: Path, criteria: EngineCriteria, engine_root: Path | None = None
) -> dict[str, Any]:
    root = isolated_output(root)
    if root.exists():
        raise ValueError("isolated_output_must_be_new")
    source = Workspace(bundle, workspace_id="graphics-read-only", limits=LIMITS)
    call = ToolCall(
        tool="game_engineer.inspect_asset",
        arguments={"folder": "."},
        call_id=str(uuid.uuid4()),
        requested_by="game-engineer:preflight",
    )
    verified = intake_engine().execute(call, ToolContext(source, frozenset({Permission.READ_WORKSPACE})))
    probe = discover_engine(engine_root)
    asset = verified.output or {}
    oracle = UnrealTechnicalOracle(probe, asset, criteria).preflight_result()
    report = {
        "status": "blocked",
        "intake": verified.to_dict(),
        "engine": probe,
        "judge": {"verdict": oracle.verdict.value, "detail": json.loads(oracle.detail)},
        "criteria": asdict(criteria),
        "engine_executed": False,
        "compile_executed": False,
        "driving_executed": False,
        "prepared_project": None,
    }
    root.mkdir(parents=True)
    if not verified.ok:
        report["correction"] = decide(
            FailureCategory.INCORRECT_SOLUTION,
            attempt_number=1,
            policy=RecoveryPolicy(
                max_attempts=criteria.max_attempts_per_defect, resource_exhausted_max_retries=1
            ),
            previous_failure=oracle.detail,
        ).to_dict()
    else:
        workspace = Workspace.create(root, "project", limits=LIMITS)
        store = LocalFilesystemArtifactStore(root=str(root / "artifacts"))
        stored = []
        hashes = {f["path"]: f["sha256"] for f in asset["manifest"]["files"]}
        hashes["manifest.json"] = asset["manifest_sha256"]
        for name in ["manifest.json", *[f["path"] for f in asset["manifest"]["files"]]]:
            content = source.read_bytes(name)
            if hashlib.sha256(content).hexdigest() != hashes[name]:
                raise RuntimeError("source_changed_after_intake")
            metadata = store.put(content)
            if store.get(metadata.content_hash) != content:
                raise RuntimeError("artifact_store_verification_failed")
            workspace.write_bytes("SourceAssets/" + name, content)
            stored.append({"file": name, "content_hash": metadata.content_hash, "bytes": len(content)})
        nonce = str(uuid.uuid4())
        workspace.write_bytes(
            ".apex-game-engineer-project.json",
            json.dumps(
                {
                    "nonce": nonce,
                    "kind": "isolated_editor_import_only",
                    "manifest_sha256": asset["manifest_sha256"],
                }
            ).encode(),
        )
        project_file = {
            "FileVersion": 3,
            "Category": "APEX isolated import probe",
            "Description": "Not a drivable game",
            "Plugins": [
                {"Name": "PythonScriptPlugin", "Enabled": True},
                {"Name": "EditorScriptingUtilities", "Enabled": True},
            ],
        }
        workspace.write_bytes("ApexVehicleProbe.uproject", json.dumps(project_file, indent=2).encode())
        script = Path(__file__).resolve().parents[2] / "scripts/game_engineer/import_vehicle.py"
        workspace.write_bytes("Tools/import_vehicle.py", script.read_bytes())
        request = {
            "nonce": nonce,
            "glb": str(workspace.root / "SourceAssets/vehicle.glb"),
            "report": str(workspace.root / "import-observation.json"),
            "destination": "/Game/APEXProbe",
        }
        workspace.write_bytes("Tools/request.json", json.dumps(request, indent=2).encode())
        report["prepared_project"] = str(workspace.root / "ApexVehicleProbe.uproject")
        report["stored_artifacts"] = stored
        report["prepared_import_command"] = [
            probe["editor"] or "UnrealEditor-Cmd",
            report["prepared_project"],
            "-unattended",
            "-nosplash",
            "-run=pythonscript",
            "-script="
            + str(workspace.root / "Tools/import_vehicle.py")
            + " "
            + str(workspace.root / "Tools/request.json"),
        ]
        report["import_command_executed"] = False
    if oracle.verdict is OracleVerdict.UNKNOWN:
        report["reason"] = "engine_or_functional_evidence_unavailable"
    else:
        report["reason"] = "asset_intake_failed"
    (root / "report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument(
        "--level", choices=("prototype", "jouable", "soigne", "photorealiste"), required=True
    )
    parser.add_argument("--criteria", type=Path)
    args = parser.parse_args()
    try:
        criteria = EngineCriteria.for_level(
            args.level, **(json.loads(args.criteria.read_text()) if args.criteria else {})
        )
        result = prepare(args.bundle, args.root, criteria, args.engine_root)
    except Exception as exc:
        result = {
            "status": "blocked",
            "reason": type(exc).__name__ + ": " + str(exc),
            "engine_executed": False,
        }
    print(json.dumps(result, indent=2, default=str))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
