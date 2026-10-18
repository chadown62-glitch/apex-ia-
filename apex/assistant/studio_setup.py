"""Composants dont Game Studio a besoin sur CE PC, et leur installation par les contrats de Game Studio.

Ce module ne télécharge rien lui-même et ne réimplémente rien de Game Studio (propriété de Codex) :
- l'état de chaque composant vient de la sonde de Game Studio (`apex.game_studio.capabilities.probe`), de
  son contrat du planificateur (`apex.game_studio.dependencies.planner_installation`), de la liste figée
  des bibliothèques d'APEX et, pour Blender, de `blender --version` ;
- un bouton d'installation n'existe que pour un composant qu'un contrat RÉEL de Game Studio couvre :
  - moteur du planificateur (`llama-cpp-python[server]`, processeur) : la liste figée facultative de Game
    Studio, `runtime/requirements-planner-cpu-windows.lock`, installée par le mécanisme de « Préparer
    APEX » (pip, empreintes exigées, roues seulement, sans dépendances), après avoir vérifié qu'elle ne
    change aucune version de bibliothèque déjà installée ;
  - poids du planificateur (Qwen2.5-Coder-7B épinglé, 5,4 Go) : la commande de Game Studio
    `python -m apex.game_studio install-planner --accept-download --accept-apache-2-license`, qui vérifie
    elle-même le serveur local, le disque, la licence et l'empreinte ;
  sans contrat, le composant dit « En attente du composant Game Studio » et pourquoi ;
- une installation est toujours demandée et confirmée par l'utilisateur, une à la fois ; elle s'exécute en
  arrière-plan et son journal est rendu tel quel.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from importlib.util import find_spec
from pathlib import Path
from typing import Any

WAITING = "En attente du composant Game Studio"
#: Nom d'import → distribution dans la liste figée.
DISTRIBUTIONS = {"PIL": "pillow"}
#: Bibliothèques importées par Game Studio que sa sonde ne vérifiait pas avant le 17/10/2026.
UNPROBED_LIBRARIES = ("jsonschema",)
#: Liste figée FACULTATIVE du moteur du planificateur, fournie par Game Studio (processeur seulement).
ENGINE_LOCK = "requirements-planner-cpu-windows.lock"
INSTALL_TIMEOUT_S = 6 * 3600
LOG_LINES = 200
#: Refus des commandes de Game Studio, en clair (le code exact reste affiché à côté).
INSTALL_REFUSALS = {
    "planner_download_and_apache_license_consent_required":
        "le téléchargement et la licence Apache-2.0 doivent être acceptés",
    "llama_cpp_server_missing_install_verified_platform_binary":
        "installez d'abord le moteur d'inférence du planificateur (llama-cpp-python)",
    "existing_planner_manifest_unverified_preserved":
        "un planificateur déjà présent ne correspond pas à la version épinglée ; il est conservé tel quel",
    "planner_insufficient_disk_space": "espace disque insuffisant pour le planificateur",
    "coder_license_changed_review_required":
        "la licence publiée du modèle a changé : rien n'est téléchargé",
    "coder_download_integrity_failed": "le fichier téléchargé n'a pas l'empreinte épinglée : il est refusé",
}

Runner = Callable[[list[str], Callable[[str], None]], tuple[int, str]]

_lock = threading.Lock()
_job: dict[str, Any] | None = None


def _runtime_dir() -> Path | None:
    """Dossier `runtime/` de l'application installée (ou du dépôt) : listes figées."""
    from apex.standalone import gpu

    folder = gpu.spec_dir()
    return folder.parent if folder is not None else None


def _pins(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return {m.group(1).lower(): m.group(2)
            for m in re.finditer(r"^([A-Za-z0-9_.-]+)==(\S+)", text, re.MULTILINE)}


def _declared(distribution: str) -> bool | None:
    """La bibliothèque figure-t-elle dans la liste figée qu'installe « Préparer APEX » ? (None : liste
    illisible.)"""
    runtime = _runtime_dir()
    if runtime is None:
        return None
    try:
        return distribution.lower() in _pins(runtime / "requirements-windows.lock")
    except OSError:
        return None


def engine_offer() -> dict[str, Any]:
    """Le moteur du planificateur peut-il être installé ici, par la liste figée de Game Studio ?"""
    runtime = _runtime_dir()
    lock = runtime / ENGINE_LOCK if runtime is not None else None
    if lock is None or not lock.is_file():
        return {"available": False, "lock": None, "source": None,
                "reason": "Game Studio ne fournit pas de liste figée du moteur pour Windows"}
    text = lock.read_text(encoding="utf-8")
    wheel = re.search(r"^llama-cpp-python @ (https://[^\s#]+)", text, re.MULTILINE)
    try:
        base = _pins(lock.parent / "requirements-windows.lock")
    except OSError:
        base = {}
    conflicts = [f"{name} {version} ≠ {base[name]}" for name, version in _pins(lock).items()
                 if name in base and base[name] != version]
    source = (f"{wheel.group(1)} (roue officielle, empreinte exigée par pip), processeur seulement"
              if wheel else None)
    if conflicts or not base:
        reason = ("la liste du moteur changerait des bibliothèques d'APEX : " + ", ".join(conflicts[:4])
                  if conflicts else "liste figée des bibliothèques d'APEX illisible")
        return {"available": False, "lock": str(lock), "source": source, "reason": reason}
    return {"available": True, "lock": str(lock), "source": source, "reason": ""}


def _planner_info() -> tuple[dict[str, Any] | None, str]:
    """Le contrat du planificateur de Game Studio (`planner_installation()`), ou pourquoi il manque."""
    try:
        from apex.game_studio.dependencies import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
            planner_installation,
        )
    except ImportError:
        return None, "Game Studio ne publie pas de contrat d'installation du planificateur"
    try:
        info = planner_installation()
    except Exception as exc:  # contrat présent mais en échec : dit tel quel, aucun bouton
        return None, f"contrat du planificateur en échec : {exc.__class__.__name__}: {exc}"[:300]
    return (info, "") if isinstance(info, dict) else (None, "contrat du planificateur illisible")


def _item(item_id: str, name: str, role: str, present: bool | None, *, detail: str = "",
          bytes_: int | None = None, source: str | None = None, license_: str | None = None,
          waiting: str = "", installed_by: str = "", manual: str | None = None) -> dict[str, Any]:
    """`waiting` : ce qu'attend Game Studio ; `installed_by` : ce qui, dans APEX, l'installe déjà."""
    state = "present" if present else "unknown" if present is None else "missing"
    reason = "" if present else installed_by or f"{WAITING} : {waiting}"
    return {"id": item_id, "name": name, "role": role, "state": state, "detail": detail, "bytes": bytes_,
            "source": source, "license": license_, "manual": manual,
            "install": {"available": False, "reason": reason}}


def _offer(item: dict[str, Any], confirmation: str) -> None:
    if item["state"] != "present":
        item["install"] = {"available": True, "reason": "", "confirmation": confirmation}


def _gigabytes(value: int) -> str:
    return f"{value / 1e9:.1f}".replace(".", ",") + " Go"


def _blender(report: dict[str, Any]) -> dict[str, Any]:
    from apex.obs.component_checks import _blender_version

    blender = report.get("vehicle_blender") or {}
    discovery = blender.get("blender_discovery") or {}
    discovered = "blender_discovery" in blender  # découverte de Game Studio (17/10/2026), sinon le PATH
    path = blender.get("blender")
    version = _blender_version(str(path)) if path else None
    found = {"explicit": "APEX_BLENDER_EXECUTABLE", "PATH": "PATH", "ProgramFiles": "Program Files"}
    if version:
        detail = f"{version} ({path}, trouvé par {found.get(str(discovery.get('source')), 'PATH')})"
    elif path:
        detail = f"{path} ne répond pas à « --version »"
    elif discovery.get("reason") == "invalid_blender_executable":
        detail = "APEX_BLENDER_EXECUTABLE ne désigne pas un exécutable"
    else:
        detail = "introuvable (APEX_BLENDER_EXECUTABLE, PATH, Program Files)" if discovered else (
            "introuvable dans le PATH")
    manual = ("Installez Blender 4.3.2 depuis blender.org dans son dossier par défaut (Program Files), ou "
              "indiquez son chemin dans la variable APEX_BLENDER_EXECUTABLE, puis redémarrez APEX."
              if discovered else
              "Installez Blender 4.3.2 depuis blender.org, ajoutez son dossier au PATH de Windows, puis "
              "redémarrez APEX.")
    return _item("blender", "Blender 4.3.2", "construit et rend les modèles 3D", bool(path and version),
                 detail=detail, source="blender.org (installé par vous)",
                 waiting="Game Studio ne l'installe pas : à installer vous-même", manual=manual)


def _planner(report: dict[str, Any]) -> dict[str, Any]:
    blender = report.get("vehicle_blender") or {}
    info, why = _planner_info()
    known = info or {}
    item = _item(
        "planner_model", "Modèle planificateur Qwen2.5-Coder-7B-Instruct (Q5_K_M)",
        "planifie chaque mission que le Cerveau propose", blender.get("coder_manifest_present"),
        detail=str(known.get("manifest") or ""), bytes_=known.get("download_bytes"),
        source=(f"Hugging Face {known['repo']}, révision {str(known.get('revision'))[:12]}"
                if known.get("repo") else None),
        license_=(f"{known['license']} (vérifiée par Game Studio avant téléchargement)"
                  if known.get("license") else None),
        waiting=why or "installez d'abord le moteur d'inférence du planificateur (llama-cpp-python)")
    if known.get("server_module_present"):
        size = _gigabytes(int(known.get("download_bytes") or 0))
        free = _gigabytes(int(known.get("required_free_bytes") or 0))
        _offer(item, f"Télécharger le modèle planificateur Qwen2.5-Coder-7B ({size}, licence Apache-2.0, "
                     f"{free} libres requis) ? Game Studio vérifie la licence puis l'empreinte du fichier.")
    return item


def _engine(report: dict[str, Any]) -> dict[str, Any]:
    blender = report.get("vehicle_blender") or {}
    engine = engine_offer()
    item = _item(
        "inference_engine", "Moteur d'inférence llama-cpp-python (serveur, processeur)",
        "exécute le planificateur sur ce PC", blender.get("llama_cpp_present"),
        detail="exécution sous Windows non qualifiée par Game Studio ; pas de CUDA dans cette roue",
        source=engine["source"] or "PyPI : sources seulement, aucune roue Windows publiée",
        waiting=engine["reason"])
    if engine["available"]:
        _offer(item, "Installer le moteur du planificateur (llama-cpp-python, processeur) depuis la liste "
                     "figée de Game Studio ? Son exécution sous Windows n'est pas encore qualifiée.")
    return item


def _items(report: dict[str, Any]) -> list[dict[str, Any]]:
    blender = report.get("vehicle_blender") or {}
    items = [_planner(report), _engine(report), _blender(report)]
    libraries = dict(blender.get("python_dependencies") or {})
    for name in UNPROBED_LIBRARIES:
        libraries.setdefault(name, find_spec(name) is not None)
    for name, present in libraries.items():
        distribution = DISTRIBUTIONS.get(str(name), str(name))
        declared = _declared(distribution)
        waiting = ("liste figée des bibliothèques illisible" if declared is None
                   else "non déclarée dans la liste figée des bibliothèques d'APEX")
        installed_by = ("déclarée dans la liste figée : « Préparer APEX » (APEX arrêté) l'installe"
                        if declared else "")
        source = "PyPI, par la liste figée d'APEX (empreintes vérifiées)" if declared else None
        manual = "Arrêtez APEX puis cliquez sur « Préparer APEX »." if declared else None
        items.append(_item(f"python:{distribution}", f"Bibliothèque Python {distribution}",
                           "importée par Game Studio", bool(present), source=source, waiting=waiting,
                           installed_by=installed_by, manual=manual))
    return items


def dependencies(*, probe: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Chaque composant, son état mesuré sur ce PC, sa taille et sa provenance quand elles sont connues,
    et son installation : offerte seulement quand un contrat de Game Studio la couvre."""
    from apex.assistant.game_studio import _probe

    with _lock:
        job = dict(_job) if _job else None
    if find_spec("apex.game_studio") is None:
        return {"installed": False, "items": [], "job": job,
                "contract": {"planner": False, "engine": False,
                             "reason": "Game Studio ne fait pas partie de cette installation d'APEX"}}
    info, why = _planner_info()
    engine = engine_offer()
    contract = {"planner": info is not None, "engine": engine["available"],
                "reason": "; ".join(r for r in (why, engine["reason"]) if r)}
    try:
        report = _probe(probe) or {}
    except Exception as exc:  # sonde en échec : aucun composant n'est compté présent
        return {"installed": True, "items": [], "contract": contract, "job": job,
                "problem": f"la sonde de Game Studio a échoué : {exc.__class__.__name__}: {exc}"[:300]}
    return {"installed": True, "items": _items(report), "contract": contract, "job": job}


def _stream(command: list[str], progress: Callable[[str], None]) -> tuple[int, str]:
    """Lance une commande d'installation et rend chacune de ses lignes au journal du travail."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(  # noqa: S603 — commandes fixes : pip de ce Python, ou Game Studio
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", env=env)
    lines: list[str] = []
    deadline = time.monotonic() + INSTALL_TIMEOUT_S
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip()
        lines.append(line)
        if line:
            progress(line)
        if time.monotonic() > deadline:
            process.kill()
            lines.append("délai d'installation dépassé")
            break
    return process.wait(), "\n".join(lines)


def _command(item_id: str) -> list[str]:
    python = sys.executable
    if item_id == "inference_engine":
        return [python, "-P", "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                "--no-warn-script-location", "--require-hashes", "--only-binary=:all:", "--no-deps", "-r",
                str(engine_offer()["lock"])]
    return [python, "-P", "-m", "apex.game_studio", "install-planner", "--accept-download",
            "--accept-apache-2-license"]


def _outcome(item_id: str, code: int, output: str) -> tuple[bool, str]:
    if item_id == "inference_engine":
        tail = [line for line in output.splitlines() if line.strip()][-1:] or [""]
        if code == 0:
            return True, "installé (empreintes vérifiées par pip)"
        return False, f"pip a échoué (code {code}) : {tail[0][:300]}"
    start = output.rfind("\n{")
    result: dict[str, Any] = {}
    with contextlib.suppress(ValueError):
        parsed = json.loads(output[start + 1:] if start >= 0 else output)
        result = parsed if isinstance(parsed, dict) else {}
    status = str(result.get("status") or "")
    if code == 0 and status == "installed_verified":
        return True, "installé et vérifié par Game Studio"
    if code == 0 and status == "already_verified":
        return True, "déjà présent et vérifié par Game Studio"
    error = str(result.get("error") or f"code {code}")
    return False, f"{INSTALL_REFUSALS[error]} ({error})" if error in INSTALL_REFUSALS else error


def start_install(item_id: str, *, confirmed: bool, probe: Callable[[], Any] | None = None,
                  background: bool = True, runner: Runner = _stream) -> dict[str, Any]:
    """L'utilisateur demande l'installation d'UN composant qu'un contrat de Game Studio couvre. Refusé sans
    confirmation explicite, pour un composant sans contrat, ou pendant une autre installation. Rend l'état
    du travail."""
    global _job
    if not confirmed:
        raise PermissionError("installation non confirmée : le téléchargement doit être accepté "
                              "explicitement")
    current = {i["id"]: i for i in dependencies(probe=probe)["items"]}.get(item_id)
    if current is None:
        raise LookupError(f"composant inconnu : {item_id}")
    if not current["install"]["available"]:
        raise RuntimeError(current["install"]["reason"] or "ce composant est déjà présent")
    command = _command(item_id)
    with _lock:
        if _job and _job["state"] == "running":
            raise RuntimeError(f"une installation est déjà en cours ({_job['item']})")
        _job = {"item": item_id, "name": current["name"], "state": "running", "log": [],
                "started_at": time.time(), "finished_at": None, "detail": ""}
        job = _job

    def progress(line: str) -> None:
        with _lock:
            job["log"] = [*job["log"], str(line)[:400]][-LOG_LINES:]

    def run() -> None:
        try:
            code, output = runner(command, progress)
            ok, detail = _outcome(item_id, code, output)
        except Exception as exc:  # l'échec est rendu tel quel, jamais une installation crue réussie
            ok, detail = False, f"{exc.__class__.__name__}: {exc}"[:400]
        with _lock:
            job.update(state="succeeded" if ok else "failed", detail=detail, finished_at=time.time())
        _forget_measurements()

    if background:
        threading.Thread(target=run, name=f"studio-setup-{item_id}", daemon=True).start()
    else:
        run()
    with _lock:
        return dict(job)


def _forget_measurements() -> None:
    """Après une installation, tout est mesuré de nouveau : bibliothèques nouvelles visibles, état des
    composants recalculé (jamais l'ancien gardé)."""
    from apex.obs import component_checks

    importlib.invalidate_caches()
    with component_checks._cache_lock:
        component_checks._cache.clear()


def _reset_for_tests() -> None:
    global _job
    with _lock:
        _job = None
