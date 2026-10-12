"""Régénère `releases/blender/apex_atelier3d.zip` (et son `.sha256`) depuis le code actuel d'APEX.

Usage : python scripts/build_blender_addon.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apex.brain.blender_addon import addon_zip  # noqa: E402


def main() -> None:
    data = addon_zip()
    target = ROOT / "releases" / "blender" / "apex_atelier3d.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    print(f"{target} : {len(data)} octets, SHA-256 {digest}")


if __name__ == "__main__":
    main()
