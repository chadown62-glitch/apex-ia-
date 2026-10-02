"""Enregistrement de DÉCOUVERTE du backend, lu par le Panel (`APEX.exe`).

Deux écrivains, un seul format (ce module) :
- `scripts/apex-launch.sh serve` écrit la phase `starting` dès qu'il a trouvé Python, AVANT le
  préflight (compose, doctor, gate), pour que le Panel affiche « en démarrage » pendant ces
  étapes au lieu de « non démarré » ; il le retire s'il échoue ;
- `apex serve` (exécuté par `exec`, donc même pid) le réécrit en phase `serving` juste avant
  d'écouter, et le retire à l'arrêt ;
- si le démarrage n'a pas lieu (préflight en échec, Startup Gate qui refuse), l'annonce devient la
  phase `refused` : code du launcher, étape, contrôles du Gate qui refusent — le Panel affiche
  « démarrage refusé » et pourquoi, au lieu de « non démarré ».

`python -m apex.discovery stop` arrête le backend enregistré (bouton « Arrêter APEX » du Panel, via
`launch.sh stop`) après avoir vérifié que ce pid est bien APEX.

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
#: `refused` : le dernier démarrage n'a pas eu lieu (préflight en échec, Startup Gate qui refuse) —
#: le Panel l'affiche avec la raison au lieu de « non démarré ». Remplacé au démarrage suivant.
PHASES = ("starting", "serving", "refused")

#: Code de sortie de `scripts/apex-launch.sh` -> étape qui a refusé le démarrage (mêmes codes que le
#: launcher, source unique : son en-tête). 1 (.env absent) et 3 (Python absent) surviennent avant que
#: Python soit trouvé : aucun enregistrement ne peut alors être écrit.
REFUSAL_STAGES = {
    2: "infrastructure",   # Docker/Compose indisponible ou `compose up` en échec
    4: "doctor",           # `apex doctor` FAIL
    5: "gate",             # Startup Gate : un contrôle MESURÉ en FAIL
    6: "gate",             # Startup Gate : une PRÉCONDITION non vérifiée
    7: "gate_error",       # `apex gate` en erreur interne (pas une décision du Gate)
}


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


def write_refused(
    host: str,
    port: int,
    project_root: Path,
    *,
    exit_code: int,
    pid: int | None = None,
    blocking: list[str] | None = None,
    unverified_blocking: list[str] | None = None,
) -> Path:
    """Remplace l'annonce `starting` du même pid par la raison du refus (phase `refused`).

    Seuls des identifiants de contrôles et un code y figurent, jamais un secret ni une sortie brute.
    Les contrôles cités sont ceux du rapport du Gate de CE démarrage (`blocking` : FAIL mesurés ;
    `unverified_blocking` : préconditions non vérifiées) — jamais déduits."""
    path = discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = record(host, port, project_root, phase="refused", pid=pid)
    previous = _read(path)
    if previous and previous.get("pid") == data["pid"]:
        data["started_at"] = previous.get("started_at", data["started_at"])
    data["refusal"] = {
        "exit_code": exit_code,
        "stage": REFUSAL_STAGES.get(exit_code, "unknown"),
        "blocking": list(blocking or []),
        "unverified_blocking": list(unverified_blocking or []),
        "at": datetime.now(UTC).isoformat(),
    }
    tmp = path.with_suffix(f".{data['pid']}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def gate_ids_since(report_path: Path, since: str | None) -> tuple[list[str], list[str]]:
    """Contrôles qui refusent le démarrage dans le rapport du Gate, s'il a été produit APRÈS `since`
    (début de ce démarrage) ; sinon rien — un rapport plus ancien ne parle pas de ce démarrage."""
    report = _read(report_path)
    if not report or not isinstance(report.get("startup"), dict):
        return [], []
    generated = _parse_time(report.get("generated_at"))
    started = _parse_time(since)
    if generated is None or started is None or generated < started.replace(microsecond=0):
        return [], []
    startup = report["startup"]
    blocking = [c for c in startup.get("blocking", []) if isinstance(c, str)]
    unverified = [
        c["check_id"] for c in startup.get("unverified_blocking", [])
        if isinstance(c, dict) and isinstance(c.get("check_id"), str)
    ]
    return blocking, unverified


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


#: Un pid d'enregistrement n'est signalé que si son processus est bien APEX : `apex serve` (après
#: `exec`) ou `apex-launch.sh` pendant le préflight. Un pid réutilisé par un autre programme n'est
#: jamais touché.
_APEX_CMDLINE_MARKERS = ("apex.cli.main", "apex-launch.sh")


def _cmdline(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return None


def _children(pid: int) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_line = (entry / "stat").read_text()
        except OSError:
            continue
        # « pid (comm) état ppid ... » : comm peut contenir des espaces/parenthèses -> après le dernier « ) ».
        fields = stat_line.rsplit(")", 1)[-1].split()
        if len(fields) > 1 and fields[1] == str(pid):
            found.append(int(entry.name))
    return found


def _alive(pid: int) -> bool:
    """Vivant = présent et pas zombie (un processus terminé que son parent n'a pas encore récolté
    n'exécute plus rien)."""
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    fields = stat_line.rsplit(")", 1)[-1].split()
    return bool(fields) and fields[0] not in ("Z", "X")


def _wait_gone(pid: int, timeout_s: float) -> bool:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return not _alive(pid)


def stop_backend(timeout_s: float = 30.0, *, out: Any = sys.stderr) -> int:
    """Arrête le backend APEX enregistré (démarré par `launch.sh serve`/`panel-serve`) — 0 arrêté ou
    rien à arrêter, 1 impossible. SIGINT d'abord (arrêt propre d'uvicorn / interruption du préflight,
    enfants directs compris), SIGTERM ensuite, SIGKILL en dernier recours, toujours annoncés."""
    import signal

    path = discovery_path()
    current = _read(path)
    if current is None:
        print("aucun backend APEX enregistré : rien à arrêter.", file=out)
        return 0
    pid = current.get("pid")
    if current.get("phase") == "refused" or not isinstance(pid, int) or pid < 1:
        with contextlib.suppress(OSError):
            path.unlink()
        print("aucun backend APEX en cours (enregistrement du dernier démarrage effacé).", file=out)
        return 0
    cmdline = _cmdline(pid) if _alive(pid) else None
    if cmdline is None or not any(m in cmdline for m in _APEX_CMDLINE_MARKERS):
        with contextlib.suppress(OSError):
            path.unlink()
        print(f"le pid {pid} enregistré n'est plus APEX (arrêt brutal précédent) : enregistrement effacé, "
              "aucun processus signalé.", file=out)
        return 0
    print(f"arrêt du backend APEX (pid {pid}, phase {current.get('phase')})...", file=out)
    for sig, wait in ((signal.SIGINT, timeout_s), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        if sig is not signal.SIGINT:
            print(f"le backend ne s'est pas arrêté : envoi de {sig.name}.", file=out)
        targets = [pid] + (_children(pid) if sig is signal.SIGINT else [])
        for target in targets:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(target, sig)
        if _wait_gone(pid, wait):
            # `apex serve` retire lui-même son enregistrement à l'arrêt ; sinon (préflight interrompu,
            # SIGKILL), il ne décrit plus rien de vivant.
            remove_discovery(pid)
            print("backend APEX arrêté.", file=out)
            return 0
    print(f"le backend APEX (pid {pid}) ne s'arrête pas.", file=out)
    return 1


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
    """`python -m apex.discovery starting --pid P --host H --port N` / `remove --pid P` /
    `refused --pid P --code C [--host H --port N]` / `stop [--timeout S]`."""
    parser = argparse.ArgumentParser(prog="python -m apex.discovery")
    sub = parser.add_subparsers(dest="command", required=True)
    starting = sub.add_parser("starting", help="annonce un démarrage en cours (launch.sh serve)")
    starting.add_argument("--pid", type=int, required=True)
    starting.add_argument("--host", default="127.0.0.1")
    starting.add_argument("--port", type=int, default=8000)
    remove = sub.add_parser("remove", help="retire l'enregistrement de ce pid")
    remove.add_argument("--pid", type=int, required=True)
    refused = sub.add_parser("refused", help="remplace l'annonce de ce pid par la raison du refus")
    refused.add_argument("--pid", type=int, required=True)
    refused.add_argument("--code", type=int, required=True)
    refused.add_argument("--host", default="127.0.0.1")
    refused.add_argument("--port", type=int, default=8000)
    stop = sub.add_parser("stop", help="arrête le backend APEX enregistré (processus vérifié)")
    stop.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "starting":
            write_discovery(args.host, args.port, Path.cwd().resolve(), phase="starting", pid=args.pid)
        elif args.command == "refused":
            previous = _read(discovery_path())
            since = previous.get("started_at") if previous and previous.get("pid") == args.pid else None
            blocking, unverified = (
                gate_ids_since(Path.cwd() / ".apex" / "environment.json", since)
                if REFUSAL_STAGES.get(args.code) == "gate" else ([], [])
            )
            write_refused(args.host, args.port, Path.cwd().resolve(), exit_code=args.code, pid=args.pid,
                          blocking=blocking, unverified_blocking=unverified)
        elif args.command == "stop":
            return stop_backend(args.timeout)
        else:
            remove_discovery(args.pid)
    except OSError as exc:
        print(f"découverte : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
