"""Enregistrement de DÉCOUVERTE du backend, lu par le Panel (`APEX.exe`).

Deux écrivains, un seul format (ce module) :
- `scripts/apex-launch.sh serve` écrit la phase `starting` dès qu'il a trouvé Python, AVANT le
  préflight (compose, doctor, gate), pour que le Panel affiche « en démarrage » pendant ces
  étapes au lieu de « non démarré » ; il le retire s'il échoue ;
- `apex serve` (exécuté par `exec`, donc même pid) le réécrit en phase `serving` juste avant
  d'écouter, et le retire à l'arrêt.

Le Panel y trouve, sans rien demander à l'utilisateur : le port, la racine du projet (où vit le
jeton `.apex/api-token`), le pid, l'heure de démarrage, les versions. Aucun secret ici. Un
enregistrement peut survivre à un arrêt brutal (`kill -9`) : le Panel ne le prend jamais pour une
preuve de disponibilité — seule une réponse HTTP authentifiée compte ; il vérifie que le pid vit.

Module volontairement léger (aucune dépendance au reste de l'API) : `launch.sh` l'appelle en
`python -m apex.discovery`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apex.version import API_VERSION, PANEL_PROTOCOL, __version__

#: Permet aux tests d'isoler le répertoire ; en production, le répertoire personnel WSL/Linux.
DISCOVERY_DIR_ENV = "APEX_DISCOVERY_DIR"
SCHEMA = 1
PHASES = ("starting", "serving")


def discovery_path() -> Path:
    base = os.environ.get(DISCOVERY_DIR_ENV)
    root = Path(base) if base else Path.home() / ".apex" / "runtime"
    return root / "backend.json"


def record(
    host: str, port: int, project_root: Path, *, phase: str = "serving", pid: int | None = None
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"phase inconnue : {phase}")
    return {
        "schema": SCHEMA,
        "service": "apex",
        "phase": phase,
        "apex_version": __version__,
        "api_version": API_VERSION,
        "panel_protocol": PANEL_PROTOCOL,
        "project_root": str(project_root),
        "token_file": ".apex/api-token",
        "host": host,
        "port": port,
        "pid": pid if pid is not None else os.getpid(),
        "started_at": datetime.now(UTC).isoformat(),
    }


def write_discovery(
    host: str, port: int, project_root: Path, *, phase: str = "serving", pid: int | None = None
) -> Path:
    """Écriture atomique (temporaire + `replace`) : le Panel ne lit jamais un fichier à moitié écrit.
    En phase `serving`, l'heure de démarrage d'un enregistrement `starting` du même pid est gardée
    (le Panel mesure la durée du démarrage complet, préflight compris)."""
    path = discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, stat.S_IRWXU)
    data = record(host, port, project_root, phase=phase, pid=pid)
    previous = _read(path)
    if previous and previous.get("pid") == data["pid"] and previous.get("phase") == "starting":
        data["started_at"] = previous.get("started_at", data["started_at"])
    tmp = path.with_suffix(f".{data['pid']}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def remove_discovery(pid: int | None = None) -> bool:
    """Retire l'enregistrement s'il est le NÔTRE (même pid) : jamais celui d'une autre instance."""
    path = discovery_path()
    current = _read(path)
    if current is None or current.get("pid") != (pid if pid is not None else os.getpid()):
        return False
    with contextlib.suppress(OSError):
        path.unlink()
        return True
    return False


def cleanup_on_shutdown(app: Any) -> Any:
    """Enveloppe ASGI : retire l'enregistrement quand le serveur signale la FIN de son arrêt
    (`lifespan.shutdown.complete`). Nécessaire parce qu'uvicorn (>= 0.29) relance le signal reçu
    (Ctrl+C, SIGTERM) APRÈS l'arrêt propre : le processus meurt alors du signal et un `finally`
    autour d'`uvicorn.run` ne s'exécute jamais (constaté : enregistrement laissé après SIGTERM)."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> Any:
        if scope.get("type") != "lifespan":
            return await app(scope, receive, send)

        async def send_and_cleanup(message: dict[str, Any]) -> None:
            if message.get("type") in ("lifespan.shutdown.complete", "lifespan.shutdown.failed"):
                remove_discovery()
            await send(message)

        return await app(scope, receive, send_and_cleanup)

    return wrapped


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    """`python -m apex.discovery starting --pid P --host H --port N` / `remove --pid P`."""
    parser = argparse.ArgumentParser(prog="python -m apex.discovery")
    sub = parser.add_subparsers(dest="command", required=True)
    starting = sub.add_parser("starting", help="annonce un démarrage en cours (launch.sh serve)")
    starting.add_argument("--pid", type=int, required=True)
    starting.add_argument("--host", default="127.0.0.1")
    starting.add_argument("--port", type=int, default=8000)
    remove = sub.add_parser("remove", help="retire l'enregistrement de ce pid")
    remove.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "starting":
            write_discovery(args.host, args.port, Path.cwd().resolve(), phase="starting", pid=args.pid)
        else:
            remove_discovery(args.pid)
    except OSError as exc:
        print(f"découverte : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
