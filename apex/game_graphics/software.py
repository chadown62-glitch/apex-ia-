"""Shared read-only Blender discovery. Presence is never execution qualification."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path


def discover_blender() -> dict:
    explicit = os.environ.get("APEX_BLENDER_EXECUTABLE")
    if explicit:
        path = Path(explicit).expanduser()
        # An invalid explicit selection must not silently run another installation.
        valid = path.is_absolute() and path.is_file() and os.access(path, os.X_OK)
        return {
            "path": str(path.resolve()) if valid else None,
            "source": "explicit",
            "reason": None if valid else "invalid_blender_executable",
        }
    path = shutil.which("blender")
    if path:
        return {"path": str(Path(path).resolve()), "source": "PATH", "reason": None}
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMFILES")
        if base and Path(base).is_absolute():
            candidates = []
            for path in (Path(base) / "Blender Foundation").glob("Blender */blender.exe"):
                match = re.fullmatch(r"Blender (\d+)\.(\d+)(?:\.(\d+))?", path.parent.name)
                if match and path.is_file():
                    candidates.append((tuple(int(x or 0) for x in match.groups()), path))
            if candidates:
                path = max(candidates)[1]
                return {"path": str(path.resolve()), "source": "ProgramFiles", "reason": None}
    return {"path": None, "source": None, "reason": "blender_missing"}
