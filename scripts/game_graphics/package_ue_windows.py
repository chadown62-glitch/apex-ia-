"""Build the UE5.8 qualification source bundle; does not package an executed Unreal project."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def package(destination: Path) -> dict:
    if destination.exists():
        raise ValueError("preserve_existing_bundle_choose_new_destination")
    # Read-only snapshot of the existing runtime. No installation or modification of the APEX core.
    files = sorted((ROOT / "apex").rglob("*.py"))
    files += [
        ROOT / "scripts/game_graphics" / name
        for name in (
            "ue_build_scene.py",
            "ue_inspect_scene.py",
            "ue_mutate_scene.py",
            "package_ue_windows.py",
        )
    ]
    files += sorted((ROOT / "scripts/game_graphics/windows").glob("*"))
    files += [ROOT / "docs/game_graphics/ue/WINDOWS.md"]
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
        if (ROOT / name).is_file():
            files.append(ROOT / name)
    entries = []
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()  # noqa: S607
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())  # noqa: S607
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr(rel, data)
            entries.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        shortcut = (
            b"@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
            b'call "%~dp0scripts\\game_graphics\\windows\\Run-APEX-UE58.cmd" %*\r\n'
            b"exit /b %ERRORLEVEL%\r\n"
        )
        archive.writestr("Run-APEX-UE58.cmd", shortcut)
        entries.append(
            {
                "path": "Run-APEX-UE58.cmd",
                "sha256": hashlib.sha256(shortcut).hexdigest(),
                "bytes": len(shortcut),
            }
        )
        manifest = {
            "kind": "APEX-UE58-source-launcher",
            "source_commit": commit,
            "source_dirty": dirty,
            "engine_executed": False,
            "files": entries,
        }
        archive.writestr("bundle-manifest.json", json.dumps(manifest, indent=2))
    return {
        "bundle": str(destination.resolve()),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "bytes": destination.stat().st_size,
        "source_commit": commit,
        "source_dirty": dirty,
        "file_count": len(entries) + 1,
        "engine_executed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output), indent=2))
