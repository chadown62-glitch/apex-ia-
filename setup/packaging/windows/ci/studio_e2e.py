"""Parcours RÉEL de création 3D d'APEX, contre un APEX installé par l'installateur et démarré par le Panel.

Pilote uniquement l'API publique d'APEX (bibliothèque standard), avec le jeton de l'installation :

1. état des composants (`GET /status`) et disponibilité de Game Studio, tels qu'APEX les mesure ;
2. calcul des missions sur le processeur si demandé (choix EXPLICITE, comme l'utilisateur dans les Paramètres) ;
3. conversation en mode 3D : « Crée une voiture sportive rouge… » ; le Cerveau doit PROPOSER une mission ;
4. le clic de l'utilisateur (`POST …/game-studio/launch`, acte humain vérifié par APEX) ;
5. suivi de la mission jusqu'à son état final (ou le délai), résultats, aperçus rendus enregistrés.

Verdicts : PASS (0) seulement si la mission a produit des fichiers ; BLOCKED (3) si un prérequis manque
(Game Studio absent, Blender, planificateur, bibliothèque, GPU…) — la raison d'APEX est rapportée telle
quelle ; FAIL (1) si APEX s'est mal comporté (pas de proposition pour une demande claire, réponse invalide…).
Les preuves vont dans `<sortie>/studio-<étiquette>.json` et `<sortie>/studio-previews/`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assistant_e2e import BLOCKED, FAIL, PASS, Api, last_assistant, wait_turn

REQUEST = "Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender."
ACTIVE = ("queued", "running")
COMPONENTS = ("apex_brain", "native_model", "gpu", "game_studio", "planner", "blender", "game_graphics",
              "game_engineer", "sandbox", "models")


def run(api: Api, out: Path, label: str, *, cpu: bool, turn_timeout_s: float, mission_timeout_s: float) -> int:
    evidence: dict[str, Any] = {"phase": "studio", "label": label, "request": REQUEST}

    def finish(verdict: int, reason: str) -> int:
        evidence["verdict"] = {PASS: "PASS", FAIL: "FAIL", BLOCKED: "BLOCKED"}[verdict]
        evidence["reason"] = reason
        (out / f"studio-{label}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        print("VERDICT", evidence["verdict"], reason)
        return verdict

    status = api.data("GET", "/status")
    evidence["components"] = {c["component"]: {"state": c["state"], "detail": c["detail"]}
                              for c in status["components"] if c["component"] in COMPONENTS}
    for name, value in evidence["components"].items():
        print(f"COMPONENT {name} {value['state']} {value['detail'][:200]}")
    # Composants de Game Studio tels que l'Atelier 3D les présente (état, taille, provenance, et pourquoi
    # aucune installation n'est offerte tant que Game Studio n'en publie pas le contrat).
    listing = api.data("GET", "/assistant/game-studio/dependencies")
    evidence["dependencies"] = listing
    print(f"NOTE contrat d'installation de Game Studio : {listing['contract']['present']} "
          f"({listing['contract']['reason'] or 'présent'})")
    for item in listing.get("items", []):
        offer = "installable" if item["install"]["available"] else item["install"]["reason"] or "—"
        print(f"DEPENDENCY {item['id']} {item['state']} {offer[:160]}")
    evidence["availability"] = api.data("GET", "/assistant/game-studio/availability")
    if not evidence["availability"].get("installed"):
        return finish(BLOCKED, "Game Studio absent de cette installation : "
                      + "; ".join(evidence["availability"].get("missing", [])))
    if cpu:
        settings = api.data("PUT", "/assistant/settings", {"tools": {"game_studio_compute": "cpu"}})
        evidence["compute"] = settings["tools"]["game_studio_compute"]

    conversation = api.data("POST", "/assistant/conversations", {"title": "Voiture sportive", "mode": "3d"})
    accepted = api.data("POST", f"/assistant/conversations/{conversation['id']}/turns", {"content": REQUEST})
    started = time.monotonic()
    turn = wait_turn(api, accepted["turn"]["id"], turn_timeout_s)
    answer = last_assistant(api, conversation["id"]) or {}
    meta = answer.get("meta") or {}
    evidence["turn"] = {"status": turn["status"], "error": turn["error"], "wall_s": round(time.monotonic() - started, 1),
                        "model_id": answer.get("model_id"), "content_head": (answer.get("content") or "")[:600],
                        "runtime": meta.get("runtime"), "game_studio": meta.get("game_studio"),
                        "tools_notes": meta.get("tools_notes")}
    proposal = meta.get("game_studio") or {}
    print("PROPOSAL", json.dumps(proposal, ensure_ascii=False)[:400])
    if proposal.get("state") == "unavailable":
        return finish(BLOCKED, f"Game Studio indisponible : {proposal.get('reason')}")
    if proposal.get("state") != "proposed":
        return finish(FAIL, f"aucune mission proposée pour une demande de création claire ({proposal or 'rien'})")

    code, launched = api.call("POST", f"/assistant/conversations/{conversation['id']}/game-studio/launch",
                              {"message_id": answer["id"]})
    evidence["launch"] = {"http": code, "response": launched}
    record = ((launched or {}).get("data") or {}).get("meta", {}).get("game_studio", {}) if code == 200 else {}
    print("LAUNCH", code, json.dumps(record, ensure_ascii=False)[:600])
    if code != 200:
        return finish(FAIL, f"le clic de lancement a échoué : HTTP {code} {launched}")
    if record.get("state") != "launched":
        why = record.get("explanation") or (record.get("error") or {}).get("message") or record
        return finish(BLOCKED, f"Game Studio a refusé la mission : {why}")

    project = record["project_id"]
    deadline = time.monotonic() + mission_timeout_s
    followed: dict[str, Any] = {}
    while True:
        followed = api.data("GET", f"/assistant/conversations/{conversation['id']}/game-studio/{project}")
        state = (followed.get("status") or {}).get("status")
        print("MISSION", state, time.strftime("%H:%M:%S"))
        if state not in ACTIVE or time.monotonic() > deadline:
            break
        time.sleep(20)
    evidence["mission"] = followed
    results = followed.get("results") or {}
    previews = out / "studio-previews"
    previews.mkdir(parents=True, exist_ok=True)
    saved = []
    for artifact in results.get("artifacts") or []:
        if artifact.get("role") != "view":
            continue
        code, payload = api.call(
            "GET", f"/assistant/conversations/{conversation['id']}/game-studio/{project}/preview/{artifact['hash']}")
        if code == 200:
            data = base64.b64decode(payload["data"]["content_base64"])
            target = previews / f"{label}-{artifact.get('name') or artifact['hash']}"
            target.write_bytes(data)
            saved.append({"file": target.name, "bytes": len(data)})
    evidence["previews"] = saved
    state = (followed.get("status") or {}).get("status")
    if state in ACTIVE:
        api.call("POST", f"/assistant/conversations/{conversation['id']}/game-studio/{project}/cancel")
        return finish(BLOCKED, f"mission encore « {state} » après {mission_timeout_s:.0f} s : annulée par le test")
    if results.get("ready") and results.get("artifact_count"):
        return finish(PASS, f"mission « {state} » : {results['artifact_count']} fichier(s), {len(saved)} aperçu(s)")
    steps = (followed.get("status") or {}).get("steps") or []
    failed = [s for s in steps if s.get("status") not in ("completed", "done", "passed")]
    detail = json.dumps(failed, ensure_ascii=False)[:600]
    return finish(BLOCKED, f"mission « {state} » sans fichiers produits ; étapes : {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="installed", help="étiquette des preuves (ex. installed, with-jsonschema)")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    home = os.environ.get("APEX_HOME") or str(Path(os.environ.get("LOCALAPPDATA", "~")) / "APEX")
    parser.add_argument("--home", default=home)
    parser.add_argument("--out", default=".")
    parser.add_argument("--cpu", action="store_true", help="calcul des missions sur le processeur (choix explicite)")
    parser.add_argument("--turn-timeout", type=float, default=1800.0)
    parser.add_argument("--mission-timeout", type=float, default=5400.0)
    args = parser.parse_args(argv)
    # Sortie UTF-8 quelle que soit la page de codes de la console (cp1252 sous Windows).
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    token_file = Path(args.home).expanduser() / ".apex" / "api-token"
    if not token_file.is_file():
        print(f"BLOCKED jeton introuvable : {token_file} (APEX n'a jamais démarré ?)")
        return BLOCKED
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    api = Api(args.base, token_file.read_text(encoding="utf-8").strip())
    try:
        return run(api, out, args.label, cpu=args.cpu, turn_timeout_s=args.turn_timeout,
                   mission_timeout_s=args.mission_timeout)
    except Exception as exc:  # un défaut du script de test ne se confond jamais avec un verdict sur APEX
        reason = f"{exc.__class__.__name__}: {exc}"
        print(f"FAIL {reason}")
        (out / f"studio-{args.label}-error.txt").write_text(reason, encoding="utf-8")
        return FAIL


if __name__ == "__main__":
    sys.exit(main())
