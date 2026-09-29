"""Enregistrement de DÉCOUVERTE du backend, lu par le Panel (`APEX.exe`).

`apex serve` (lancé par `scripts/apex-launch.sh serve`) écrit `~/.apex/runtime/backend.json` avant
d'écouter et le retire à l'arrêt. Le Panel y trouve, sans rien demander à l'utilisateur : le port,
la racine du projet (où vit le jeton `.apex/api-token`), le pid, l'heure de démarrage, les versions.

Aucun secret ici : le jeton n'est JAMAIS recopié (seul son chemin relatif est connu du Panel, qui le
lit en mémoire). Un enregistrement peut survivre à un arrêt brutal (`kill -9`) : le Panel ne le
prend jamais pour une preuve de disponibilité — seule une réponse HTTP authentifiée compte.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apex.version import API_VERSION, PANEL_PROTOCOL, __version__

#: Permet aux tests d'isoler le répertoire ; en production, le répertoire personnel WSL/Linux.
DISCOVERY_DIR_ENV = "APEX_DISCOVERY_DIR"
SCHEMA = 1


def discovery_path() -> Path:
    base = os.environ.get(DISCOVERY_DIR_ENV)
    root = Path(base) if base else Path.home() / ".apex" / "runtime"
    return root / "backend.json"


def record(host: str, port: int, project_root: Path) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "service": "apex",
        "apex_version": __version__,
        "api_version": API_VERSION,
        "panel_protocol": PANEL_PROTOCOL,
        "project_root": str(project_root),
        "token_file": ".apex/api-token",
        "host": host,
        "port": port,
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
    }


def write_discovery(host: str, port: int, project_root: Path) -> Path:
    """Écriture atomique (temporaire + `replace`) : le Panel ne lit jamais un fichier à moitié écrit."""
    path = discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, stat.S_IRWXU)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record(host, port, project_root), indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def remove_discovery() -> bool:
    """Retire l'enregistrement s'il est le NÔTRE (même pid) : jamais celui d'une autre instance."""
    path = discovery_path()
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if current.get("pid") != os.getpid():
        return False
    with contextlib.suppress(OSError):
        path.unlink()
        return True
    return False
