"""Parcours RÉEL de l'assistant APEX 0.4.0 contre un APEX démarré (installé par l'installateur, lancé par le Panel).

Pilote uniquement l'API publique d'APEX (bibliothèque standard, aucun accès direct à la base) avec le jeton de
l'installation (`<APEX_HOME>/.apex/api-token`) :

- `run` : projet + consignes + pièce jointe texte, conversation en mode Finance (tour réel avec le modèle installé :
  la réponse, le modèle, les tokens, la durée et les outils RÉELLEMENT appelés sont relevés tels quels), puis une
  conversation en mode Code (écriture d'un fichier dans l'espace de travail ; exécution seulement si la sandbox
  est vérifiée par APEX), réglage enregistré ; les identifiants sont gardés pour la phase suivante ;
- `verify` (après arrêt et redémarrage d'APEX par le Panel) : mêmes conversations, mêmes messages, même réglage.

Verdicts : PASS (code 0), FAIL (1 — APEX s'est mal comporté), BLOCKED (3 — un prérequis manque, ex. aucun
modèle disponible : jamais converti en PASS). Les preuves sont écrites dans `<sortie>/assistant-<phase>.json`.
Ce script ne choisit pas à la place du modèle : un outil non appelé est rapporté « non appelé », pas simulé.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PASS, FAIL, BLOCKED = 0, 1, 3


class Api:
    def __init__(self, base: str, token: str) -> None:
        if not base.startswith(("http://", "https://")):
            raise ValueError(f"adresse d'APEX invalide : {base}")
        self.base = base.rstrip("/") + "/api/v1"
        self.token = token

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={  # noqa: S310 — http(s) vérifié
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 — http(s) vérifié
                return response.status, json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw

    def data(self, method: str, path: str, body: Any = None) -> Any:
        status, payload = self.call(method, path, body)
        if status >= 400 or not isinstance(payload, dict) or payload.get("status") != "real":
            raise RuntimeError(f"{method} {path} -> HTTP {status} : {payload}")
        return payload["data"]


def wait_turn(api: Api, turn_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        turn = api.data("GET", f"/assistant/turns/{turn_id}")
        if turn["status"] not in ("queued", "running"):
            return turn
        if time.monotonic() > deadline:
            api.call("POST", f"/assistant/turns/{turn_id}/cancel")
            raise TimeoutError(f"tour {turn_id} encore « {turn['status']} » après {timeout_s:.0f} s (annulation demandée)")
        time.sleep(3)


def last_assistant(api: Api, conversation_id: str) -> dict[str, Any] | None:
    messages = api.data("GET", f"/assistant/conversations/{conversation_id}/messages")
    return next((m for m in reversed(messages) if m["role"] == "assistant"), None)


def turn_facts(turn: dict[str, Any], message: dict[str, Any] | None) -> dict[str, Any]:
    meta = (message or {}).get("meta") or {}
    return {
        "turn_status": turn["status"], "turn_error": turn["error"],
        "message_status": (message or {}).get("status"), "model_id": (message or {}).get("model_id"),
        "content_chars": len((message or {}).get("content") or ""), "content_head": ((message or {}).get("content") or "")[:600],
        "tokens_completion": meta.get("tokens_completion"), "elapsed_s": meta.get("elapsed_s"), "runtime": meta.get("runtime"),
        "tools_offered": meta.get("tools_offered"), "tools_notes": meta.get("tools_notes"),
        "tools_called": [{"tool": s["tool"], "ok": s["ok"], "output": (s.get("observation") or {}).get("output")}
                         for s in turn.get("steps") or []],
        "attachments_used": meta.get("attachments"),
    }


def run(api: Api, out: Path, timeout_s: float) -> int:
    evidence: dict[str, Any] = {"phase": "run", "checks": []}

    def check(name: str, ok: bool, detail: Any) -> None:
        evidence["checks"].append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        print(f"CHECK {name} {'PASS' if ok else 'FAIL'} {json.dumps(detail, ensure_ascii=False)[:400]}")

    models = api.data("GET", "/assistant/models")
    evidence["models"] = models
    if not any(m["available"] for m in models):
        evidence["verdict"] = "BLOCKED"
        evidence["reason"] = "aucun modèle disponible (détection réelle d'APEX) : « APEX.exe --install-model » n'a pas abouti"
        (out / "assistant-run.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BLOCKED", evidence["reason"])
        return BLOCKED

    project = api.data("POST", "/assistant/projects", {
        "name": "Validation 0.4.0", "description": "Parcours CI de l'assistant",
        "instructions": "Réponds en français. Montants en euros, deux décimales."})
    attachment = api.data("POST", f"/assistant/projects/{project['id']}/attachments", {
        "filename": "charges.csv", "content_base64": base64.b64encode(b"poste;montant\nloyer;950\n").decode()})
    status, refused = api.call("POST", f"/assistant/projects/{project['id']}/attachments", {
        "filename": "devis.pdf", "content_base64": base64.b64encode(b"%PDF-1.4").decode()})
    check("A1_piece_jointe_texte_acceptee_pdf_refuse", attachment["filename"] == "charges.csv" and status == 422,
          {"accepted": attachment["filename"], "pdf_http": status, "pdf_reason": refused})

    finance = api.data("POST", "/assistant/conversations", {"title": "Prêt immobilier", "project_id": project["id"], "mode": "finance"})
    accepted = api.data("POST", f"/assistant/conversations/{finance['id']}/turns", {
        "content": "Quelle est la mensualité d'un prêt de 200 000 € sur 20 ans à 3,5 % par an ?"})
    started = time.monotonic()
    turn = wait_turn(api, accepted["turn"]["id"], timeout_s)
    message = last_assistant(api, finance["id"])
    facts = turn_facts(turn, message) | {"wall_s": round(time.monotonic() - started, 1)}
    evidence["finance_turn"] = facts
    check("A2_tour_finance_reponse_reelle_du_modele",
          turn["status"] == "done" and facts["message_status"] == "real" and facts["content_chars"] > 0 and bool(facts["model_id"]),
          {k: facts[k] for k in ("turn_status", "turn_error", "message_status", "model_id", "content_chars", "tokens_completion", "elapsed_s", "wall_s")})
    loan = [t for t in facts["tools_called"] if t["tool"] == "finance.loan" and t["ok"]]
    # Observation, pas exigence : c'est le modèle qui décide d'appeler l'outil ; s'il l'appelle, le résultat doit être exact.
    evidence["finance_loan_called"] = bool(loan)
    if loan:
        check("A3_outil_finance_loan_exact", (loan[0]["output"] or {}).get("monthly_payment") == 1159.92, loan[0]["output"])
    else:
        print("NOTE finance.loan non appelé par le modèle (rapporté tel quel, pas un échec d'APEX)")

    code = api.data("POST", "/assistant/conversations", {"title": "Somme", "project_id": project["id"], "mode": "code"})
    accepted = api.data("POST", f"/assistant/conversations/{code['id']}/turns", {
        "content": "Crée le fichier somme.py qui définit add(a, b) et affiche add(2, 3). Exécute-le si tu le peux."})
    turn = wait_turn(api, accepted["turn"]["id"], timeout_s)
    facts = turn_facts(turn, last_assistant(api, code["id"]))
    workspace = api.data("GET", f"/assistant/conversations/{code['id']}/workspace")
    facts["workspace_files"] = workspace["files"]
    evidence["code_turn"] = facts
    check("A4_tour_code_termine_explicitement", turn["status"] in ("done", "failed") and facts["message_status"] in ("real", "error"),
          {k: facts[k] for k in ("turn_status", "turn_error", "message_status", "tools_offered", "tools_notes", "tools_called", "workspace_files")})
    wrote = [t for t in facts["tools_called"] if t["tool"] == "fs.write" and t["ok"]]
    if wrote:
        listed = {f["path"] for f in workspace["files"]}
        check("A5_fichier_ecrit_present_dans_l_espace", any(p.endswith(".py") for p in listed), sorted(listed))
    ran = [t for t in facts["tools_called"] if t["tool"] == "code.run"]
    evidence["code_run_called"] = bool(ran)
    evidence["code_run_offered"] = "code.run" in (facts["tools_offered"] or [])

    settings = api.data("PUT", "/assistant/settings", {"appearance": {"theme": "dune"}, "runtime": {"generation_timeout_s": 1500}})
    check("A6_reglage_enregistre", settings["appearance"]["theme"] == "dune" and settings["runtime"]["generation_timeout_s"] == 1500,
          {"theme": settings["appearance"]["theme"], "timeout": settings["runtime"]["generation_timeout_s"]})

    snapshot = {cid: api.data("GET", f"/assistant/conversations/{cid}/messages") for cid in (finance["id"], code["id"])}
    (out / "assistant-state.json").write_text(json.dumps({"project_id": project["id"], "conversations": snapshot},
                                                         ensure_ascii=False), encoding="utf-8")
    failed = [c for c in evidence["checks"] if c["result"] == "FAIL"]
    evidence["verdict"] = "FAIL" if failed else "PASS"
    (out / "assistant-run.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("VERDICT", evidence["verdict"])
    return FAIL if failed else PASS


def verify(api: Api, out: Path) -> int:
    state_file = out / "assistant-state.json"
    if not state_file.is_file():
        print("BLOCKED phase run absente ou bloquée : rien à vérifier")
        return BLOCKED
    state = json.loads(state_file.read_text(encoding="utf-8"))
    checks = []
    for cid, before in state["conversations"].items():
        status, payload = api.call("GET", f"/assistant/conversations/{cid}/messages")
        after = payload.get("data") if status == 200 and isinstance(payload, dict) else None
        same = after is not None and ([(m["id"], m["content"], m["status"]) for m in before]
                                      == [(m["id"], m["content"], m["status"]) for m in after])
        checks.append({"check": f"V1_messages_conserves_{cid[:8]}", "result": "PASS" if same else "FAIL",
                       "detail": {"before": len(before), "after": len(after) if after is not None else f"HTTP {status}"}})
    settings = api.data("GET", "/assistant/settings")
    ok = settings["appearance"]["theme"] == "dune" and settings["runtime"]["generation_timeout_s"] == 1500
    checks.append({"check": "V2_reglages_conserves", "result": "PASS" if ok else "FAIL",
                   "detail": {"theme": settings["appearance"]["theme"], "timeout": settings["runtime"]["generation_timeout_s"]}})
    project = api.call("GET", f"/assistant/projects/{state['project_id']}")
    checks.append({"check": "V3_projet_conserve", "result": "PASS" if project[0] == 200 else "FAIL", "detail": project[0]})
    for c in checks:
        print(f"CHECK {c['check']} {c['result']} {json.dumps(c['detail'], ensure_ascii=False)}")
    failed = [c for c in checks if c["result"] == "FAIL"]
    verdict = "FAIL" if failed else "PASS"
    (out / "assistant-verify.json").write_text(json.dumps({"phase": "verify", "checks": checks, "verdict": verdict},
                                                          ensure_ascii=False, indent=2), encoding="utf-8")
    print("VERDICT", verdict)
    return FAIL if failed else PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=("run", "verify"))
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--home", default=os.environ.get("APEX_HOME") or str(Path(os.environ.get("LOCALAPPDATA", "~")) / "APEX"))
    parser.add_argument("--out", default=".")
    parser.add_argument("--timeout", type=float, default=1800.0, help="délai maximal d'un tour (s) ; CPU sans GPU : plusieurs minutes")
    args = parser.parse_args(argv)
    # Sortie UTF-8 quelle que soit la page de codes de la console (cp1252 sous Windows) : le signe moins U+2212 du résultat de
    # finance.loan faisait planter l'affichage de la vérification A3 (constaté le 17/10/2026, run 38986394260).
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    token_file = Path(args.home).expanduser() / ".apex" / "api-token"
    if not token_file.is_file():
        print(f"BLOCKED jeton introuvable : {token_file} (APEX n'a jamais démarré ?)")
        return BLOCKED
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    api = Api(args.base, token_file.read_text(encoding="utf-8").strip())
    try:
        return run(api, out, args.timeout) if args.phase == "run" else verify(api, out)
    except Exception as exc:  # un défaut du script de test ne se confond jamais avec un verdict sur APEX
        reason = f"{exc.__class__.__name__}: {exc}"
        print(f"FAIL {reason}")
        (out / f"assistant-{args.phase}-error.txt").write_text(reason, encoding="utf-8")
        return FAIL


if __name__ == "__main__":
    sys.exit(main())
