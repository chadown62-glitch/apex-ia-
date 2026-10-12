import json
import shutil
from pathlib import Path

import pytest

from apex.game_engineer.__main__ import LIMITS
from apex.game_engineer.assets import inspect_bundle
from apex.game_engineer.tools import intake_engine
from apex.tools.engine import ToolContext
from apex.tools.model import Permission, ToolCall
from apex.tools.workspace import Workspace

BUNDLE = Path(__file__).resolve().parents[1] / "docs/game_engineer/evidence/received"


def workspace(path):
    return Workspace(path, workspace_id="real-graphics-intake", limits=LIMITS)


def test_actual_received_glb_uv_material_and_pivots():
    result = inspect_bundle(workspace(BUNDLE), ".")
    assert result["status"] == "verified_intake"
    assert result["verified_files"] == 9
    assert len(result["wheel_nodes"]) == 4
    assert result["materials"] >= 4
    assert result["engine_import_verified"] is False


def test_actual_glb_corruption_rejected(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    path = tmp_path / "bundle" / "vehicle.glb"
    with path.open("ab") as handle:
        handle.write(b"corrupt")
    with pytest.raises(ValueError, match="asset_integrity_failed"):
        inspect_bundle(workspace(tmp_path / "bundle"), ".")


def test_updated_hash_cannot_hide_invalid_glb_header(tmp_path):
    import hashlib

    shutil.copytree(BUNDLE, tmp_path / "bundle")
    root = tmp_path / "bundle"
    path = root / "vehicle.glb"
    content = b"NOT A GLB"
    path.write_bytes(content)
    data = json.loads((root / "manifest.json").read_text())
    for entry in data["files"]:
        if entry["path"] == "vehicle.glb":
            entry.update(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
    (root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="invalid_glb_header"):
        inspect_bundle(workspace(root), ".")


def test_unagreed_schema_refused(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "bundle")
    root = tmp_path / "bundle"
    (root / "vehicle-asset-v1.schema.json").write_text("{}")
    with pytest.raises(ValueError, match="unagreed_vehicle_schema"):
        inspect_bundle(workspace(root), ".")


def test_bundle_symlink_refused(tmp_path):
    from apex.tools.model import ToolFailure

    shutil.copytree(BUNDLE, tmp_path / "bundle")
    external = tmp_path / "outside.json"
    shutil.copyfile(BUNDLE / "spec.json", external)
    root = tmp_path / "bundle"
    (root / "spec.json").unlink()
    (root / "spec.json").symlink_to(external)
    with pytest.raises(ToolFailure, match="lien symbolique"):
        inspect_bundle(workspace(root), ".")


def test_tool_engine_read_permission_is_enforced():
    call = ToolCall("game_engineer.inspect_asset", {"folder": "."}, "call", "test")
    engine = intake_engine()
    denied = engine.execute(call, ToolContext(workspace(BUNDLE), frozenset()))
    assert not denied.ok
    assert denied.error.code.value == "permission_denied"
    allowed = engine.execute(call, ToolContext(workspace(BUNDLE), frozenset({Permission.READ_WORKSPACE})))
    assert allowed.ok, allowed.error


def test_tool_engine_rejects_parent_traversal():
    call = ToolCall("game_engineer.inspect_asset", {"folder": ".."}, "call", "test")
    result = intake_engine().execute(
        call, ToolContext(workspace(BUNDLE), frozenset({Permission.READ_WORKSPACE}))
    )
    assert not result.ok
    assert result.error.code.value == "path_refused"
