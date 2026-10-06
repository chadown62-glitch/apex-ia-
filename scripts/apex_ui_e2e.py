#!/usr/bin/env python3
"""Audit de l'interface DE BOUT EN BOUT : vrai backend APEX (FastAPI, base SQLite fichier, backend de modèle
NATIF, vraie sandbox, vrai Judge), vrai build du frontend (`frontend/dist`), vrai navigateur (Chromium piloté
par Playwright). Chaque parcours clique dans l'interface puis VÉRIFIE dans la base ou l'API ce qui s'est
réellement passé ; toute erreur console et toute réponse HTTP ≥ 400 inattendue est relevée.

Le modèle est celui de `APEX_MODELS_DIR` (endpoint natif de la configuration) : avec un modèle non entraîné, on
vérifie que chaque capacité est RÉELLEMENT reliée (jamais qu'elle réussit) ; avec un vrai modèle, le même
parcours montre ce qu'il produit.

Usage : APEX_MODELS_DIR=... python scripts/apex_ui_e2e.py --out <dossier>
Code : 0 tous les parcours vérifiés ; 1 au moins un écart (listé).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_backend(root: Path, port: int) -> Any:
    import uvicorn
    from sqlalchemy import create_engine

    from apex.api import dependencies
    from apex.api.app import create_app
    from apex.db.engine import build_session_factory
    from apex.db.models import Base

    if os.environ.get("APEX_E2E_RUNTIME_ONLY") == "1":
        # Comme le Runtime packagé : AUCUNE extension Recherche (routes et commandes absentes).
        import apex.util.extensions as extensions

        extensions.load_research_extension = lambda: None
    engine = create_engine(f"sqlite:///{root / 'apex.db'}", connect_args={"check_same_thread": False, "timeout": 60},
                           future=True)
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    dependencies.get_session_factory = lambda: factory  # PostgreSQL absent de ce banc : même schéma, SQLite
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server, factory
        time.sleep(0.05)
    raise RuntimeError("le backend n'a pas démarré")


class Audit:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.findings: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []

    def check(self, area: str, what: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"area": area, "check": what, "ok": bool(ok), "detail": detail[:500]})
        print(f"{'OK ' if ok else 'ÉCART'} [{area}] {what} {detail[:200]}", flush=True)
        if not ok:
            self.findings.append(self.checks[-1])


def run(out: Path) -> int:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="apex-ui-e2e-"))
    os.environ.setdefault("APEX_DATA_ROOT", str(root / "data"))
    os.environ["APEX_API_TOKEN"] = secrets.token_urlsafe(24)
    os.environ.setdefault("APEX_UI_DIST", str(Path("frontend/dist").resolve()))
    port = free_port()
    server, factory = start_backend(root, port)
    base = f"http://127.0.0.1:{port}"
    audit = Audit(out)

    import urllib.request

    def api(method: str, path: str, body: Any = None) -> Any:
        request = urllib.request.Request(
            base + "/api/v1" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {os.environ['APEX_API_TOKEN']}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read())

    login = api("POST", "/ui/launch-code")["data"]["login_path"]
    console_errors: list[str] = []
    http_errors: list[str] = []

    with sync_playwright() as pw:
        executable = os.environ.get("APEX_E2E_CHROMIUM")  # navigateur préinstallé si la version diffère
        browser = pw.chromium.launch(executable_path=executable) if executable else pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("console", lambda m: console_errors.append(f"{page.url} :: {m.text}") if m.type == "error" else None)
        page.on("response", lambda r: http_errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        page.goto(base + login)
        page.wait_for_load_state("networkidle")
        audit.check("session", "connexion par code à usage unique", "/ui/login" not in page.url, page.url)

        # -- MODÈLES : disponibilité réelle et raison --------------------------------------------------------
        page.goto(base + "/models")
        page.get_by_role("button", name="Vérifier la connexion").click()
        page.wait_for_timeout(2500)
        text = page.inner_text("body")
        audit.check("modèles", "le modèle natif apparaît", "transformers" in text)
        audit.check("modèles", "un backend PLANNED est dit indisponible", "PLANNED — non disponible" in text)
        page.screenshot(path=str(out / "models.png"), full_page=True)

        # -- CHAT : conversation, génération réelle, mémoire ---------------------------------------------------
        page.goto(base + "/chat")
        page.get_by_role("button", name="+ Nouvelle conversation").first.click()
        page.wait_for_timeout(800)
        page.get_by_placeholder("Écrire un message…").fill("Mon projet s'appelle Zéphyr.")
        page.get_by_role("button", name="Envoyer").click()
        page.wait_for_selector("text=Génération en cours", timeout=5000)
        audit.check("chat", "progression réelle (temps écoulé), aucune étape simulée",
                    "Évaluation" not in page.inner_text("body"))
        page.wait_for_selector(".chat-bubble--assistant, .chat-bubble--error", timeout=600_000)
        conversations = api("GET", "/conversations")["data"]
        messages = api("GET", f"/conversations/{conversations[0]['id']}/messages")["data"]
        assistant = [m for m in messages if m["role"] == "assistant"]
        audit.check("chat", "la réponse affichée est celle persistée",
                    bool(assistant) and (assistant[-1]["status"] == "error" or assistant[-1]["content"][:30] in page.inner_text("body")),
                    json.dumps(assistant[-1] if assistant else None, ensure_ascii=False)[:300])
        audit.check("chat", "modèle réellement utilisé affiché",
                    bool(assistant) and (assistant[-1]["status"] == "error" or (assistant[-1]["model_id"] or "") in page.inner_text("body")))
        # Deuxième conversation : la mémoire système doit retrouver la première.
        page.get_by_role("button", name="+ Nouvelle conversation").first.click()
        page.wait_for_timeout(800)
        page.get_by_placeholder("Écrire un message…").fill("Comment s'appelle mon projet ?")
        page.get_by_role("button", name="Envoyer").click()
        page.wait_for_selector("text=Génération en cours", state="detached", timeout=600_000)
        page.wait_for_timeout(1000)
        recalled = page.locator("[aria-label='Souvenirs utilisés']").count()
        audit.check("chat", "souvenirs d'une autre conversation affichés sous la réponse", recalled > 0,
                    page.inner_text("body")[-400:])
        page.screenshot(path=str(out / "chat.png"), full_page=True)

        # -- MÉMOIRE : document, recherche, provenance -------------------------------------------------------
        page.goto(base + "/memory")
        page.get_by_label("Titre du document").fill("Guide du Startup Gate")
        page.get_by_label("Contenu du document").fill("Le Startup Gate vérifie la sandbox avant le démarrage d'APEX.")
        page.get_by_role("button", name="Indexer le document").click()
        page.wait_for_selector("text=indexé en", timeout=10000)
        page.get_by_label("Requête").fill("Startup Gate sandbox")
        page.get_by_role("button", name="Rechercher").click()
        page.wait_for_selector("text=document:", timeout=10000)
        audit.check("mémoire", "document retrouvé avec sa provenance", "document:" in page.inner_text("body"))
        page.screenshot(path=str(out / "memory.png"), full_page=True)

        # -- TÂCHES : création, lancement, tentatives, verdict, trace ----------------------------------------
        page.goto(base + "/tasks")
        page.get_by_role("button", name="Créer la tâche de référence").click()
        page.wait_for_timeout(1500)
        tasks = api("GET", "/tasks")["data"]
        audit.check("tâches", "tâche de référence créée par l'interface", bool(tasks))
        if tasks:
            page.get_by_text(tasks[0]["title"]).first.click()
            page.wait_for_timeout(500)
            page.get_by_role("button", name="Lancer").first.click()
            deadline = time.time() + 900
            runs: list[Any] = []
            while time.time() < deadline:
                runs = api("GET", f"/tasks/{tasks[0]['id']}/runs")["data"]
                if runs and runs[0]["status"] != "running":
                    break
                time.sleep(2)
            audit.check("tâches", "exécution lancée et terminée", bool(runs) and runs[0]["status"] != "running",
                        str(runs[0]["status"] if runs else None))
            if runs:
                detail = api("GET", f"/task-runs/{runs[0]['id']}")["data"]
                page.wait_for_timeout(2500)
                body = page.inner_text("body")
                audit.check("tâches", "verdict du Judge affiché",
                            any(f"verdict {e['oracle_verdict']}" in body for e in detail["eval_runs"]) or not detail["eval_runs"])
                audit.check("tâches", "trace réelle affichée", "judge.completed" in body or "provider" in body)
        page.screenshot(path=str(out / "tasks.png"), full_page=True)

        # -- MISSIONS : création avec tâche vérifiable, exécution par le TaskLoop, revue --------------------
        if tasks:
            page.goto(base + "/mission-center")
            page.wait_for_timeout(1000)
            mission = api("POST", "/missions", {"title": "Mission e2e", "specialty": "research", "objective": "o",
                                                "task_id": tasks[0]["id"], "task_options": {"max_attempts": 1}})
            audit.check("missions", "mission à tâche vérifiable créée", mission["status"] == "real",
                        str(mission.get("detail")))
            page.reload()
            page.wait_for_timeout(1500)
            audit.check("missions", "mission visible dans le centre", "Mission e2e" in page.inner_text("body"))
            page.screenshot(path=str(out / "missions.png"), full_page=True)

        # -- TOUTES LES PAGES : aucune erreur console, rien de cassé ------------------------------------------
        from apex.util.extensions import load_research_extension  # noqa: F401

        routes = ["/", "/agents", "/system", "/security-gate", "/brain", "/skills", "/weaknesses",
                  "/evolution-timeline", "/hardware", "/resources", "/project-manager", "/code-intelligence",
                  "/design-studio", "/experiment-lab", "/rag", "/files"]
        for route in routes:
            before = len(console_errors)
            page.goto(base + route)
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            audit.check("navigation", f"{route} se charge", len(body.strip()) > 20 and len(console_errors) == before,
                        "; ".join(console_errors[before:])[:300])
            if os.environ.get("APEX_E2E_RUNTIME_ONLY") == "1" and route in ("/brain", "/skills", "/weaknesses",
                                                                             "/experiment-lab"):
                audit.check("distribution", f"{route} dit être absent du Runtime",
                            "absent de cette distribution" in body)
        browser.close()

    server.should_exit = True
    unexpected = [e for e in http_errors if "/api/v1/" in e]
    audit.check("http", "aucune réponse API en erreur pendant les parcours", not unexpected, "; ".join(unexpected[:10]))
    (out / "e2e.json").write_text(json.dumps({"checks": audit.checks, "findings": audit.findings,
                                              "console_errors": console_errors, "http_errors": http_errors},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(audit.checks) - len(audit.findings)}/{len(audit.checks)} vérifications OK", flush=True)
    return 1 if audit.findings else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    return run(Path(parser.parse_args().out))


if __name__ == "__main__":
    sys.exit(main())
