"""Composants dont Game Studio a besoin sur CE PC, et leur installation quand Game Studio la propose.

Ce module ne télécharge et n'installe RIEN lui-même :
- l'état de chaque composant vient de la sonde de Game Studio (`apex.game_studio.capabilities.probe`,
  propriété de Codex), de la liste figée des bibliothèques d'APEX et, pour Blender, de `blender --version` ;
- un bouton d'installation n'existe que si Game Studio publie un CONTRAT d'installation qui le couvre :
  le module `apex.game_studio.setup` (à écrire par Codex, voir `docs/REPRISE_CODEX_GAME_STUDIO.md` §0) avec

      plan() -> list[dict]
          {"id": "planner_model" | "inference_engine" | "blender" | "python:<distribution>",
           "available": bool, "reason": str | None, "bytes": int | None, "source": str | None,
           "license": str | None, "confirmation": str | None}
      install(item_id: str, *, confirmed: bool, progress: Callable[[str], None]) -> dict
          {"ok": bool, "detail": str}

  Sans ce contrat, chaque composant absent dit « En attente du composant Game Studio » et pourquoi.
- une installation est toujours demandée par l'utilisateur, confirmée explicitement, une à la fois ; elle
  s'exécute en arrière-plan et son journal est rendu tel quel.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from collections.abc import Callable
from importlib.util import find_spec
from typing import Any

WAITING = "En attente du composant Game Studio"
#: Nom d'import → distribution dans la liste figée.
DISTRIBUTIONS = {"PIL": "pillow"}
#: Bibliothèques importées par Game Studio que sa sonde ne vérifie pas (constaté sous Windows, 17/10/2026).
UNPROBED_LIBRARIES = ("jsonschema",)
#: Taille du GGUF épinglé telle que Game Studio la contrôle après téléchargement (`fetch_coder`, relevé le
#: 17/10/2026) : remplacée par celle du contrat dès que Game Studio la publie.
PLANNER_BYTES = 5_444_831_232
LOG_LINES = 200

_lock = threading.Lock()
_job: dict[str, Any] | None = None


def _setup_contract() -> tuple[Any | None, str]:
    """(module du contrat, ou None ; pourquoi il manque)."""
    if find_spec("apex.game_studio") is None:
        return None, "Game Studio ne fait pas partie de cette installation d'APEX"
    try:
        from apex.game_studio import (  # type: ignore[import-untyped, import-not-found, attr-defined, unused-ignore]
            setup,
        )
    except ImportError:
        return None, "Game Studio ne publie pas encore de contrat d'installation (apex.game_studio.setup)"
    if not (callable(getattr(setup, "plan", None)) and callable(getattr(setup, "install", None))):
        return None, "apex.game_studio.setup ne fournit pas plan() et install()"
    return setup, ""


def _plan(setup: Any) -> tuple[dict[str, dict[str, Any]], str | None]:
    try:
        items = setup.plan()
    except Exception as exc:  # contrat présent mais en échec : dit tel quel, aucun bouton
        return {}, f"plan() de Game Studio a échoué : {exc.__class__.__name__}: {exc}"[:300]
    if not isinstance(items, list):
        return {}, "plan() de Game Studio n'a pas rendu une liste"
    return {str(i["id"]): i for i in items if isinstance(i, dict) and i.get("id")}, None


def _declared(distribution: str) -> bool | None:
    """La bibliothèque figure-t-elle dans la liste figée qu'installe « Préparer APEX » ? (None : liste
    illisible.)"""
    from apex.standalone import gpu

    folder = gpu.spec_dir()
    if folder is None:
        return None
    try:
        text = (folder.parent / "requirements-windows.lock").read_text(encoding="utf-8")
    except OSError:
        return None
    return re.search(rf"^{re.escape(distribution.lower())}==", text.lower(), re.MULTILINE) is not None


def _planner_facts() -> dict[str, Any]:
    """Identité du planificateur épinglé lue dans le code de Game Studio, et son descripteur s'il
    existe."""
    facts: dict[str, Any] = {}
    names = (("repo", "CODER_REPO"), ("revision", "CODER_COMMIT"), ("file", "CODER_FILE"),
             ("sha256", "CODER_SHA256"))
    # Game Studio absent ou ses dépendances manquantes : identité inconnue, jamais devinée.
    with contextlib.suppress(Exception):
        from apex.game_graphics import (  # type: ignore[import-untyped, import-not-found, attr-defined, unused-ignore]
            models,
        )

        facts = {key: getattr(models, name, None) for key, name in names}
    # Descripteur illisible : l'état vient de la sonde, la taille reste inconnue.
    with contextlib.suppress(Exception):
        from apex.game_studio.capabilities import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
            coder_manifest,
        )

        manifest = coder_manifest()
        if manifest.is_file():
            descriptor = json.loads(manifest.read_text(encoding="utf-8"))
            facts["installed_bytes"] = descriptor.get("bytes")
            facts["path"] = descriptor.get("path")
    return facts


def _item(item_id: str, name: str, role: str, present: bool | None, *, detail: str = "",
          bytes_: int | None = None, source: str | None = None, license_: str | None = None,
          waiting: str = "", installed_by: str = "", manual: str | None = None) -> dict[str, Any]:
    """`waiting` : ce qu'attend Game Studio ; `installed_by` : ce qui, dans APEX, l'installe déjà."""
    state = "present" if present else "unknown" if present is None else "missing"
    reason = "" if present else installed_by or f"{WAITING} : {waiting}"
    return {"id": item_id, "name": name, "role": role, "state": state, "detail": detail, "bytes": bytes_,
            "source": source, "license": license_, "manual": manual,
            "install": {"available": False, "reason": reason}}


def _items(report: dict[str, Any]) -> list[dict[str, Any]]:
    from apex.obs.component_checks import _blender_version

    blender = report.get("vehicle_blender") or {}
    planner = _planner_facts()
    revision = str(planner.get("revision") or "")[:12]
    planner_source = (f"Hugging Face {planner['repo']}, révision {revision}, fichier {planner.get('file')}"
                      if planner.get("repo") else None)
    items = [
        _item("planner_model", "Modèle planificateur Qwen2.5-Coder-7B-Instruct (Q5_K_M)",
              "planifie chaque mission que le Cerveau propose", blender.get("coder_manifest_present"),
              detail=str(planner.get("path") or ""), bytes_=planner.get("installed_bytes") or PLANNER_BYTES,
              source=planner_source, license_="Apache-2.0 (vérifiée par Game Studio avant téléchargement)",
              waiting="Game Studio sait télécharger et vérifier ce modèle (licence puis empreinte SHA-256) "
                      "mais ne propose pas encore de point d'entrée d'installation"),
        _item("inference_engine", "Moteur d'inférence llama-cpp-python (serveur)",
              "exécute le planificateur sur ce PC", blender.get("llama_cpp_present"),
              source="PyPI : sources seulement, aucune roue Windows publiée",
              waiting="aucune source binaire Windows n'est encore choisie par Game Studio "
                      "(processeur ou CUDA)"),
    ]
    path = blender.get("blender")
    version = _blender_version(str(path)) if path else None
    items.append(_item(
        "blender", "Blender 4.3.2", "construit et rend les modèles 3D",
        bool(path and version), detail=f"{version} ({path})" if version else (
            f"{path} ne répond pas à « --version »" if path else "introuvable dans le PATH"),
        source="blender.org (installé par vous)",
        waiting="Game Studio ne cherche Blender que dans le PATH",
        manual="Installez Blender 4.3.2 depuis blender.org, ajoutez son dossier au PATH de Windows, puis "
               "redémarrez APEX."))
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
    et son installation : possible seulement si le contrat de Game Studio la couvre."""
    from apex.assistant.game_studio import _probe

    setup, why = _setup_contract()
    contract = {"present": setup is not None, "module": "apex.game_studio.setup", "reason": why}
    with _lock:
        job = dict(_job) if _job else None
    if find_spec("apex.game_studio") is None:
        return {"installed": False, "items": [], "contract": contract, "job": job}
    try:
        report = _probe(probe) or {}
    except Exception as exc:  # sonde en échec : aucun composant n'est compté présent
        return {"installed": True, "items": [], "contract": contract, "job": job,
                "problem": f"la sonde de Game Studio a échoué : {exc.__class__.__name__}: {exc}"[:300]}
    items = _items(report)
    if setup is not None:
        planned, problem = _plan(setup)
        if problem:
            contract["reason"] = problem
        for item in items:
            offer = planned.get(item["id"])
            if offer is None or item["state"] == "present":
                continue
            for key, field in (("bytes", "bytes"), ("source", "source"), ("license", "license")):
                if offer.get(field):
                    item[key] = offer[field]
            if offer.get("available"):
                item["install"] = {"available": True, "reason": "",
                                   "confirmation": offer.get("confirmation") or ""}
            else:
                why_not = offer.get("reason") or "installation indisponible"
                item["install"]["reason"] = f"{WAITING} : {why_not}"
    return {"installed": True, "items": items, "contract": contract, "job": job}


def start_install(item_id: str, *, confirmed: bool, probe: Callable[[], Any] | None = None,
                  background: bool = True) -> dict[str, Any]:
    """L'utilisateur demande l'installation d'UN composant que Game Studio sait installer. Refusé sans
    confirmation explicite, sans contrat, pour un composant que le contrat ne couvre pas, ou pendant une
    autre installation. Rend l'état du travail."""
    global _job
    if not confirmed:
        raise PermissionError("installation non confirmée : le téléchargement doit être accepté "
                              "explicitement")
    current = {i["id"]: i for i in dependencies(probe=probe)["items"]}.get(item_id)
    if current is None:
        raise LookupError(f"composant inconnu : {item_id}")
    if not current["install"]["available"]:
        raise RuntimeError(current["install"]["reason"] or "ce composant est déjà présent")
    setup, why = _setup_contract()
    if setup is None:  # pragma: no cover - vérifié par dependencies() juste avant
        raise RuntimeError(f"{WAITING} : {why}")
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
            outcome = setup.install(item_id, confirmed=True, progress=progress)
            ok = bool(isinstance(outcome, dict) and outcome.get("ok"))
            detail = str((outcome or {}).get("detail") or "") if isinstance(outcome, dict) else ""
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
    """Après une installation, l'état des composants est mesuré de nouveau (jamais l'ancien gardé)."""
    from apex.obs import component_checks

    with component_checks._cache_lock:
        component_checks._cache.clear()


def _reset_for_tests() -> None:
    global _job
    with _lock:
        _job = None
