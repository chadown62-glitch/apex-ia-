"""Isolated Windows CI preparation. Never runs on the user's installation.

Builds the existing runtime, installs its locked dependencies into the same pinned
NuGet CPython used by APEX, and calls its qualification module. No weights unless
--download-pinned-qwen is explicitly supplied by workflow_dispatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLENDER_SHA = "933a62a770c941e316535f893629ff106ddd2aa5a956f0081ec557439073d4f6"
BLENDER_ZIP = "blender-4.3.2-windows-x64.zip"


def download(urls: list[str], target: Path, digest: str) -> dict:
    errors = []
    for url in urls:
        try:
            # Fixed release URLs and SHA256; no user URL accepted.
            with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as stream:  # noqa: S310
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            with target.open("rb") as verified:
                actual = hashlib.file_digest(verified, "sha256").hexdigest()
            if actual != digest:
                raise RuntimeError("download_hash_mismatch:" + target.name)
            return {"url": url, "bytes": target.stat().st_size, "sha256": actual}
        except OSError as exc:
            errors.append(str(exc))
    raise RuntimeError("download_failed:" + repr(errors))


def run(command: list[str], log: Path, *, env=None, cwd=None, timeout=1800):
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(  # noqa: S603 - fixed qualification commands, no shell
            command, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=cwd, timeout=timeout, check=False
        )
    print(
        json.dumps(
            {
                "command": command,
                "exit_code": result.returncode,
                "elapsed_s": time.monotonic() - started,
                "log": str(log),
            }
        ),
        flush=True,
    )
    if result.returncode:
        raise RuntimeError("qualification_command_failed:" + str(log))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--download-pinned-qwen", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("native_windows_required_no_wine_substitute")
    output = args.output.resolve()
    normalized = str(output).replace("\\", "/").casefold()
    if "c:/gameproject" in normalized:
        raise ValueError("personal_game_project_protected")
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((ROOT / "setup/packaging/runtime/python-runtime.json").read_text())
    provenance = {
        "python": download(
            [manifest["python"]["url"]], output / "python.nupkg", manifest["python"]["sha256"]
        )
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2))
    with zipfile.ZipFile(output / "python.nupkg") as archive:
        archive.extractall(output / "python")
    python = output / "python/tools/python.exe"
    sys.path.insert(0, str(ROOT))
    from runtime_build.build import build_runtime_from_worktree

    build_runtime_from_worktree(output / "runtime")
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "-r",
            str(ROOT / "setup/packaging/runtime/requirements-windows.lock"),
            "-r",
            str(ROOT / "setup/packaging/runtime/requirements-planner-cpu-windows.lock"),
        ],
        output / "python-install.log",
    )
    provenance["blender"] = download(
        [
            "https://download.blender.org/release/Blender4.3/" + BLENDER_ZIP,
            "https://mirrors.ocf.berkeley.edu/blender/release/Blender4.3/" + BLENDER_ZIP,
        ],
        output / BLENDER_ZIP,
        BLENDER_SHA,
    )
    with zipfile.ZipFile(output / BLENDER_ZIP) as archive:
        archive.extractall(output / "blender")
    blender = output / "blender/blender-4.3.2-windows-x64/blender.exe"
    env = {
        **os.environ,
        "APEX_HOME": str(output / "profile"),
        "APEX_BLENDER_EXECUTABLE": str(blender),
        "PYTHONPATH": str(output / "runtime"),
        "PYTHONUTF8": "1",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    for key in (
        "APEX_CONFIG",
        "APEX_API_TOKEN",
        "APEX_DATABASE_URL",
        "APEX_DATA_ROOT",
        "APEX_GAME_STUDIO_MODEL_MANIFEST",
        "APEX_UI_DIST",
    ):
        env.pop(key, None)
    if args.download_pinned_qwen:
        run(
            [
                str(python),
                "-m",
                "apex.game_studio",
                "install-planner",
                "--accept-download",
                "--accept-apache-2-license",
            ],
            output / "planner-install.log",
            env=env,
            cwd=output / "runtime",
            timeout=2400,
        )
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2))
    run(
        [str(python), "-m", "apex.game_studio.qualification", "--output", str(output / "evidence")],
        output / "qualification.log",
        env=env,
        cwd=output / "runtime",
        timeout=4200,
    )


if __name__ == "__main__":
    main()
