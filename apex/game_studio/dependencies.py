"""Explicit planner provisioning contract for the existing launcher/Panel.

This module never installs Python packages or downloads weights during probing.
The Panel may call the CLI only after a human accepts the displayed download.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from apex.game_studio.capabilities import coder_manifest

WEIGHT_BYTES = 5_444_831_232
DISK_MARGIN = 1024**3


def planner_installation() -> dict:
    from apex.game_graphics.models import CODER_COMMIT, CODER_REPO, CODER_SHA256

    try:
        server = importlib.util.find_spec("llama_cpp.server") is not None
    except (ImportError, ValueError):
        server = False
    manifest = coder_manifest()
    return {
        "schema_version": 1,
        "manifest": str(manifest),
        "manifest_present": manifest.is_file(),
        "server_module_present": server,
        "repo": CODER_REPO,
        "revision": CODER_COMMIT,
        "sha256": CODER_SHA256,
        "license": "Apache-2.0",
        "download_bytes": WEIGHT_BYTES,
        "required_free_bytes": WEIGHT_BYTES + DISK_MARGIN,
        "requires_explicit_consent": True,
        "automatic_download": False,
        "gpu_qualified": False,
        "reason": None if server else "llama_cpp_server_missing_install_verified_platform_binary",
    }


def install_planner(*, accept_download: bool, accept_license: bool) -> dict:
    # Consent is checked before imports which can probe installed libraries, mkdir or network.
    if not accept_download or not accept_license:
        raise ValueError("planner_download_and_apache_license_consent_required")
    from apex.game_graphics.models import fetch_coder, file_sha256
    from apex.game_graphics.resources import _locked
    from apex.game_graphics.studio.execution import refuse_personal_project

    info = planner_installation()
    if not info["server_module_present"]:
        raise RuntimeError(info["reason"])
    manifest = Path(info["manifest"]).resolve()
    refuse_personal_project(manifest)
    if manifest.name != "coder-model.json":
        raise ValueError("planner_install_requires_coder_model_json_filename")
    directory = manifest.parent
    directory.mkdir(parents=True, exist_ok=True)
    with _locked(directory / "planner-install.lock"):
        if manifest.exists():
            # Never overwrite a user-installed manifest, even if invalid.
            existing = json.loads(manifest.read_text())
            if (
                existing.get("repo") != info["repo"]
                or existing.get("commit") != info["revision"]
                or existing.get("sha256") != info["sha256"]
                or existing.get("license") != info["license"]
                or existing.get("license_reviewed_before_download") is not True
                or file_sha256(Path(existing["path"])) != info["sha256"]
            ):
                raise RuntimeError("existing_planner_manifest_unverified_preserved")
            return {"status": "already_verified", "manifest": str(manifest)}
        if shutil.disk_usage(directory).free < info["required_free_bytes"]:
            raise RuntimeError("planner_insufficient_disk_space")
        installed = fetch_coder(directory)
        if installed.resolve() != manifest:
            raise RuntimeError("unexpected_planner_manifest_location")
    return {
        "status": "installed_verified",
        "manifest": str(manifest),
        "gpu_qualified": False,
        "generation_qualified": False,
    }
