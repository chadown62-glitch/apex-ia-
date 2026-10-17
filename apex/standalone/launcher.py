"""Lanceur d'APEX AUTONOME — ce que `scripts/apex-launch.sh` fait dans WSL, sans WSL, sans Docker, sans shell.

`python -m apex.standalone serve` (bouton « Démarrer APEX » du Panel) :
  verrou d'instance -> journal `APEX_HOME/.apex/runtime/launch.log` -> annonce « en démarrage » au Panel ->
  schéma de la base embarquée (`apex db upgrade`) -> `apex doctor` -> `apex serve` (qui exécute LUI-MÊME le
  Startup Gate, jamais contourné) ; mêmes codes de sortie et même enregistrement de découverte que
  `launch.sh`, pour que le Panel affiche « démarrage refusé » et pourquoi.
`python -m apex.standalone stop` : arrêt PROPRE demandé à l'API (jeton), puis — seulement s'il reste sans
  effet et si le pid est bien le Python d'APEX — arrêt forcé.
`python -m apex.standalone status` : ce qui est enregistré et si l'API répond (JSON).
`python -m apex.standalone model install` : poids du modèle natif par défaut (Hugging Face, révision figée).
`python -m apex.standalone gpu status|install|verify|disable|enable|remove` : accélération GPU FACULTATIVE
  (PyTorch CUDA en surcouche du runtime, `apex.standalone.gpu`) ; activée par `serve` seulement si elle est
  installée, activée et vérifiée à nouveau sur ce PC — sinon calcul sur le processeur, raison écrite.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from apex.standalone import DEFAULT_PORT, apply_environment, paths
from apex.standalone.process import (
    SingleInstanceLock,
    alive,
    image_path,
    same_executable,
    terminate,
    wait_exit,
)

# Mêmes codes que scripts/apex-launch.sh (le Panel les interprète déjà) ; 4 doctor, 5/6 Startup Gate.
EXIT_OK = 0
EXIT_DOCTOR_FAIL = 4
EXIT_GATE_FAIL = 5
EXIT_GATE_UNVERIFIED = 6
EXIT_ALREADY_RUNNING = 8
EXIT_NOT_STOPPED = 9
EXIT_DB_SCHEMA = 10
EXIT_INTERNAL = 99

#: Modèle natif par défaut d'APEX autonome — celui que déclare `config.standalone.yaml`
#: (`runtime: transformers`, `served_model`), révision figée (même que la validation réelle).
DEFAULT_MODEL = {
    "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "name": "qwen2.5-coder-1.5b-instruct",
    "revision": "2e1fd397ee46e1388853d2af2c993145b0f1098a",
}


def _log(message: str) -> None:
    print(f"[apex-standalone] {time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def _redirect_output(log_path: Path) -> None:
    """Sortie du démarrage dans `launch.log` (le précédent gardé en `.1`), descripteurs 1 et 2 compris : les
    sous-processus (sondes, sandbox) y écrivent aussi. Lu par le bouton « Journal du démarrage »."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        with contextlib.suppress(OSError):
            os.replace(log_path, log_path.with_suffix(log_path.suffix + ".1"))
    handle = open(log_path, "ab", buffering=0)  # noqa: SIM115 — vit avec le processus
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            if stream is not None:
                stream.flush()
    with contextlib.suppress(OSError, ValueError):
        os.dup2(handle.fileno(), 1)
        os.dup2(handle.fileno(), 2)
    # Nouveaux flux texte UTF-8 sur les descripteurs redirigés : sous Windows, les flux d'origine sont
    # liés à la console (cachée) et à sa page de codes — écrire un « é » dans un fichier par eux échouerait.
    out = open(1, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)  # noqa: SIM115
    sys.stdout = out
    sys.stderr = open(2, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)  # noqa: SIM115


def serve(*, host: str = "127.0.0.1", port: int = DEFAULT_PORT, log: bool = False) -> int:
    from apex.discovery import discovery_path, remove_discovery, write_discovery, write_refused

    apply_environment()
    p = paths()
    p.ensure()
    os.chdir(p.home)  # jeton `.apex/api-token` et rapport du Gate sous APEX_HOME, jamais à côté du programme
    lock = SingleInstanceLock(p.runtime_dir / "launch.lock")
    if not lock.acquire():
        print("APEX est déjà en cours de démarrage ou d'exécution pour cet utilisateur — rien n'est relancé.",
              file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    try:
        if log:
            _redirect_output(p.launch_log)
        _log(f"démarrage d'APEX autonome (données : {p.home} ; programme : {Path(__file__).resolve().parents[2]})")
        try:
            write_discovery(host, port, Path.cwd(), phase="starting")
        except OSError as exc:
            _log(f"annonce au Panel impossible ({exc}) — il dira « non démarré » jusqu'à l'écoute de l'API")

        # Avant tout import de torch : surcouche CUDA seulement si installée, activée et vérifiée ici.
        from apex.standalone import gpu

        _log(f"accélération GPU : {gpu.describe(gpu.activate(p.home))}")

        _log("schéma de la base embarquée (apex db upgrade)…")
        from apex.cli.main import main as apex_main

        if apex_main(["db", "upgrade"]) != 0:
            _log("le schéma de la base n'a pas pu être mis à jour — démarrage refusé")
            write_refused(host, port, Path.cwd(), exit_code=EXIT_DB_SCHEMA)
            return EXIT_DB_SCHEMA

        _log("apex doctor…")
        if apex_main(["doctor"]) != 0:
            _log("apex doctor a signalé un échec (FAIL) — démarrage refusé (voir le rapport ci-dessus)")
            write_refused(host, port, Path.cwd(), exit_code=EXIT_DOCTOR_FAIL)
            return EXIT_DOCTOR_FAIL

        _log(f"apex serve (Startup Gate, puis API sur http://{host}:{port})…")
        code = apex_main(["serve", "--host", host, "--port", str(port)])
        if code == 0:
            _log("APEX arrêté proprement.")
            return EXIT_OK
        record = _read_json(discovery_path())
        refusal = (record or {}).get("refusal") or {}
        if (record or {}).get("phase") == "refused" and refusal.get("exit_code") in (EXIT_GATE_FAIL,
                                                                                     EXIT_GATE_UNVERIFIED):
            return int(refusal["exit_code"])
        remove_discovery()
        return code
    except Exception as exc:  # jamais une pile brute pour le Panel : la cause, dans le journal
        _log(f"erreur interne du lanceur : {exc.__class__.__name__}: {exc}")
        with contextlib.suppress(Exception):
            write_refused(host, port, Path.cwd(), exit_code=EXIT_INTERNAL)
        return EXIT_INTERNAL
    finally:
        lock.release()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _token(record: dict[str, Any]) -> str | None:
    root = Path(str(record.get("project_root") or ""))
    try:
        return (root / str(record.get("token_file") or ".apex/api-token")).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _post_shutdown(record: dict[str, Any], timeout_s: float = 5.0) -> str:
    token = _token(record)
    if not token:
        return "jeton introuvable"
    url = f"http://{record.get('host', '127.0.0.1')}:{record.get('port', DEFAULT_PORT)}/api/v1/system/shutdown"
    request = urllib.request.Request(url, method="POST", data=b"",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 — 127.0.0.1
            return "ok" if response.status in (200, 202) else f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return f"injoignable ({exc.__class__.__name__})"


def stop(*, timeout_s: float = 30.0) -> int:
    from apex.discovery import discovery_path, remove_discovery

    apply_environment()
    path = discovery_path()
    record = _read_json(path)
    if record is None:
        print("aucun backend APEX enregistré : rien à arrêter.")
        return 0
    pid = record.get("pid")
    if record.get("phase") == "refused" or not isinstance(pid, int) or not alive(pid):
        with contextlib.suppress(OSError):
            path.unlink()
        print("aucun backend APEX en cours (enregistrement du dernier démarrage effacé).")
        return 0
    print(f"arrêt d'APEX (pid {pid}, phase {record.get('phase')})…")
    outcome = _post_shutdown(record) if record.get("phase") == "serving" else "en démarrage"
    if outcome == "ok" and wait_exit(pid, timeout_s):
        remove_discovery(pid)
        print("APEX arrêté proprement.")
        return 0
    # Arrêt propre impossible ou sans effet : arrêt forcé SEULEMENT si ce pid porte bien le Python d'APEX
    # (un pid réutilisé par un autre programme n'est jamais touché).
    image = image_path(pid)
    if not same_executable(image, sys.executable):
        print(f"arrêt propre : {outcome} ; le pid {pid} n'est pas le Python d'APEX ({image or 'inconnu'}) — "
              "aucun processus touché.", file=sys.stderr)
        return EXIT_NOT_STOPPED
    print(f"arrêt propre : {outcome} — arrêt forcé du processus {pid}.")
    terminate(pid)
    if wait_exit(pid, 10.0):
        with contextlib.suppress(OSError):
            path.unlink()
        print("APEX arrêté (forcé).")
        return 0
    print(f"le processus {pid} ne s'arrête pas.", file=sys.stderr)
    return EXIT_NOT_STOPPED


def status() -> int:
    """Ce qui est enregistré, si le pid vit et si l'API répond avec le jeton — JSON (0 répond, 1 sinon)."""
    from apex.discovery import discovery_path

    apply_environment()
    p = paths()
    record = _read_json(discovery_path())
    out: dict[str, Any] = {"home": str(p.home), "record": record, "alive": False, "api": None}
    if record and isinstance(record.get("pid"), int):
        out["alive"] = alive(record["pid"])
        token = _token(record)
        if token and out["alive"]:
            url = f"http://{record.get('host')}:{record.get('port')}/api/v1/status"
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 — 127.0.0.1
                    out["api"] = json.loads(response.read().decode("utf-8")).get("data")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                out["api"] = {"error": exc.__class__.__name__}
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if isinstance(out["api"], dict) and "error" not in out["api"] else 1


def model_install(*, repo: str | None = None, name: str | None = None, revision: str | None = None) -> int:
    """Télécharge (Hugging Face, action explicite) les poids du modèle natif dans `APEX_HOME/models`."""
    apply_environment()
    p = paths()
    p.ensure()
    from apex.models.fetch import fetch_model

    repo = repo or DEFAULT_MODEL["repo"]
    name = name or DEFAULT_MODEL["name"]
    revision = revision or (DEFAULT_MODEL["revision"] if repo == DEFAULT_MODEL["repo"] else None)
    _log(f"téléchargement du modèle {repo} ({revision or 'dernière révision'}) vers {p.models_dir / name}…")
    try:
        target = fetch_model(repo, models_dir=p.models_dir, name=name, revision=revision)
    except Exception as exc:
        _log(f"échec du téléchargement : {exc.__class__.__name__}: {exc}"[:500])
        return 1
    _log(f"modèle installé : {target}")
    return 0


def gpu_command(command: str, *, variant: str | None = None) -> int:
    """`gpu …` : chaque geste rend un compte rendu JSON ; rien n'est installé ni retiré sans lui."""
    from apex.standalone import gpu

    apply_environment()
    p = paths()
    p.ensure()
    if command == "status":
        print(json.dumps(gpu.status(p.home), indent=2, ensure_ascii=False, default=str))
        return 0
    if command in ("disable", "enable"):
        gpu.set_enabled(command == "enable", p.home)
        state = "activée" if command == "enable" else "désactivée (calcul sur le processeur)"
        print(json.dumps({"ok": True, "reason": f"accélération GPU {state} au prochain démarrage d'APEX"},
                         ensure_ascii=False))
        return 0
    if command == "install":
        code, report = gpu.install(p.home, requested=variant)
    elif command == "verify":
        code, report = gpu.verify(p.home)
    else:
        code, report = gpu.remove(p.home)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apex.standalone", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    serve_cmd = sub.add_parser("serve", help="démarrer APEX autonome (préflight + Startup Gate + API)")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_cmd.add_argument("--log", action="store_true", help="sortie dans APEX_HOME/.apex/runtime/launch.log")
    stop_cmd = sub.add_parser("stop", help="arrêter APEX (proprement, puis forcé si besoin)")
    stop_cmd.add_argument("--timeout", type=float, default=30.0)
    sub.add_parser("status", help="état enregistré et réponse de l'API (JSON)")
    model_cmd = sub.add_parser("model", help="modèle natif")
    model_sub = model_cmd.add_subparsers(dest="model_command", required=True)
    install = model_sub.add_parser("install", help="télécharger les poids du modèle par défaut")
    install.add_argument("--repo")
    install.add_argument("--name")
    install.add_argument("--revision")
    install.add_argument("--log", action="store_true", help="sortie dans APEX_HOME/.apex/runtime/model-install.log")
    gpu_cmd = sub.add_parser("gpu", help="accélération GPU facultative (PyTorch CUDA)")
    gpu_cmd.add_argument("gpu_command",
                         choices=("status", "install", "verify", "disable", "enable", "remove"))
    gpu_cmd.add_argument("--variant", help="variante imposée (ex. cu128) ; défaut : d'après le pilote")
    gpu_cmd.add_argument("--log", action="store_true",
                         help="sortie dans APEX_HOME/.apex/runtime/gpu-install.log")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(host=args.host, port=args.port, log=args.log)
    if args.command == "stop":
        return stop(timeout_s=args.timeout)
    if args.command == "status":
        return status()
    if args.command == "gpu":
        if args.log:
            apply_environment()
            _redirect_output(paths().runtime_dir / "gpu-install.log")
        return gpu_command(args.gpu_command, variant=args.variant)
    if args.log:
        apply_environment()
        _redirect_output(paths().runtime_dir / "model-install.log")
    return model_install(repo=args.repo, name=args.name, revision=args.revision)
