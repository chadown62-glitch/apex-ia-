"""Première boucle d'exécution RÉELLE d'APEX, observée de l'extérieur par son API (CI windows-desktop, WSL2).

Ce script ne produit aucune donnée de la boucle : il demande à l'APEX réellement démarré (par le Panel,
`APEX.exe --start` -> `launch.sh panel-serve` -> Startup Gate) d'exécuter la tâche de référence, puis lit
ce que la base a enregistré. Le modèle est le vrai Ollama de la distribution ; la sandbox est celle que le
runtime sélectionne ; le verdict est celui du Judge Tier. Aucune réponse simulée n'existe ici.

Phases (une par invocation, l'APEX étant redémarré entre deux par le Panel) :
  run          tâche de référence -> TaskRun -> attente du résultat ; preuves 1 à 8
  verify       après redémarrage : la même TaskRun, ses EvalRun et Experience relues à l'identique ; 9
  interrupt    démarre une TaskRun et rend la main sans attendre (le Panel arrête APEX juste après)
  interrupted  après redémarrage : cette TaskRun est « interrupted », jamais « running » ni reprise ; 10

Codes : 0 preuves de la phase vérifiées ; 1 preuve contredite ; 3 étape BLOQUÉE par l'environnement.
Exécuté sous le compte de l'utilisateur APEX dans la distribution (lit ~/.apex/runtime/backend.json et le
jeton `.apex/api-token` du dépôt, comme le Panel). Le jeton n'est jamais affiché."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE_DIR = Path.home() / "apex-first-loop"
RUN_FILE = STATE_DIR / "run.json"
INTERRUPT_FILE = STATE_DIR / "interrupt.json"
RUN_TIMEOUT_S = 45 * 60

results: list[tuple[str, str, str]] = []


def proof(number: str, label: str, ok: bool | None, detail: str) -> None:
    verdict = "BLOCKED" if ok is None else ("PASS" if ok else "FAIL")
    results.append((number, verdict, label))
    print(f"[first-loop] {verdict:7} {number:>2}. {label} — {detail}", flush=True)


def finish() -> int:
    print("[first-loop] ------------------------------------------------------------")
    for number, verdict, label in results:
        print(f"[first-loop] {verdict:7} {number:>2}. {label}")
    verdicts = {v for _, v, _ in results}
    return 1 if "FAIL" in verdicts else (3 if "BLOCKED" in verdicts else 0)


class Api:
    def __init__(self) -> None:
        record = json.loads(
            (Path.home() / ".apex" / "runtime" / "backend.json").read_text(encoding="utf-8")
        )
        if record.get("phase") != "serving":
            raise SystemExit(f"[first-loop] APEX n'est pas en service (phase {record.get('phase')!r})")
        self.base = f"http://{record['host']}:{record['port']}/api/v1"
        token_path = Path(record["project_root"]) / record.get("token_file", ".apex/api-token")
        self._token = token_path.read_text(encoding="utf-8").strip()
        self.pid = record.get("pid")

    def call(
        self, method: str, path: str, body: dict | None = None, timeout_s: float = 60
    ) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


def show_status(api: Api) -> dict[str, dict]:
    _, payload = api.call("GET", "/status")
    data = payload.get("data") or {}
    components = {c["component"]: c for c in data.get("components", [])}
    for name in ("database", "ollama", "sandbox", "brain"):
        c = components.get(name, {})
        print(f"[first-loop] /status {name:9} {c.get('state')} — {c.get('detail')}")
    gate = data.get("gate") or {}
    print(
        f"[first-loop] /status gate : startup_authorized={gate.get('startup_authorized')} "
        f"phase_2_authorized={gate.get('phase_2_authorized')}"
    )
    return components


def parse_gpu_query(text: str) -> str:
    """Sortie de `nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,
    nounits` -> texte lisible ; vide ou illisible -> « GPU non mesuré » (jamais une valeur supposée)."""
    rows = []
    for line in (text or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        name, used, total, util = parts
        rows.append(f"{name} : VRAM {used}/{total} Mio, GPU {util} %")
    return " | ".join(rows) if rows else "GPU non mesuré"


def parse_ollama_ps(payload: dict) -> str:
    """Réponse d'Ollama `GET /api/ps` -> modèles chargés, taille et part réellement en VRAM (rapportées par
    Ollama) ; aucun modèle chargé -> le dire."""
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return "Ollama : état des modèles chargés illisible"
    if not models:
        return "Ollama : aucun modèle chargé"
    parts = []
    for m in models:
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        share = f"{100 * vram / size:.0f} %" if size else "?"
        parts.append(f"{m.get('name')} {size / 1e9:.1f} Go dont {vram / 1e9:.1f} Go en VRAM ({share})")
    return "Ollama : " + " ; ".join(parts)


def gpu_facts() -> str:
    for exe in ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi"):
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return parse_gpu_query(out.stdout)
    return "GPU non mesuré (nvidia-smi absent)"


def ollama_facts(url: str = "http://127.0.0.1:11434/api/ps") -> str:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # adresse déclarée par config.yaml
            return parse_ollama_ps(json.loads(response.read()))
    except (OSError, ValueError):
        return "Ollama : /api/ps injoignable"


def host_facts() -> str:
    """Charge et mémoire de la distribution, lues dans /proc (run 21 : l'API n'a pas répondu en 60 s pendant
    l'inférence CPU — mesuré ici à chaque relevé, jamais supposé)."""
    try:
        load = Path("/proc/loadavg").read_text().split()[:3]
        mem = {
            line.split(":")[0]: int(line.split()[1]) // 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.split(":")[0] in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
        }
        system = (
            f"charge {'/'.join(load)} ; mémoire dispo {mem.get('MemAvailable')}/{mem.get('MemTotal')} Mo ; "
            f"swap utilisé {mem.get('SwapTotal', 0) - mem.get('SwapFree', 0)} Mo"
        )
    except (OSError, ValueError, IndexError):
        system = "charge/mémoire illisibles"
    return f"{system} ; {gpu_facts()} ; {ollama_facts()}"


def wait_terminal(api: Api, run_id: str) -> dict:
    """Relève la TaskRun toutes les 30 s jusqu'à un statut terminal. Une API qui ne répond pas à temps est
    MESURÉE (latence, charge, mémoire) et le relevé continue : elle n'est jamais confondue avec un résultat."""
    started = time.monotonic()
    deadline = started + RUN_TIMEOUT_S
    detail: dict = {}
    while time.monotonic() < deadline:
        asked = time.monotonic()
        try:
            status, payload = api.call("GET", f"/task-runs/{run_id}", timeout_s=120)
            detail = payload.get("data") or {}
            print(
                f"[first-loop] t={asked - started:5.0f}s TaskRun {run_id[:8]} : {detail.get('status')} "
                f"({len(detail.get('eval_runs') or [])} tentative(s) jugée(s)) — API en "
                f"{time.monotonic() - asked:.1f} s ; {host_facts()}",
                flush=True,
            )
            if status != 200 or detail.get("status") != "running":
                return detail
        except (OSError, ValueError) as exc:
            print(
                f"[first-loop] t={asked - started:5.0f}s API sans réponse après {time.monotonic() - asked:.0f} s "
                f"({exc.__class__.__name__}) ; {host_facts()}",
                flush=True,
            )
        time.sleep(30)
    print(f"[first-loop] délai de {RUN_TIMEOUT_S} s atteint : TaskRun toujours « {detail.get('status')} »")
    return detail


def load_state(path: Path, number: str, label: str) -> dict | None:
    """Une phase précédente sans résultat enregistré rend cette preuve BLOQUÉE — jamais un plantage muet."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        proof(number, label, None, f"{path.name} absent : la phase précédente n'a rien enregistré")
        return None


def print_trace(detail: dict, eval_runs: list, experiences: list) -> None:
    """La chaîne d'une exécution, chaque maillon par son identifiant RÉEL lu dans la base."""
    exp_by_eval = {x.get("eval_run_id"): x for x in experiences}
    print(f"[first-loop] TRACE TASK {detail.get('task_id')} -> RUN {detail.get('id')}")
    for i, e in enumerate(eval_runs, 1):
        x = exp_by_eval.get(e.get("id")) or {}
        print(
            f"[first-loop] TRACE   tentative {i} : {e.get('model_id')} -> génération {e.get('tokens_completion')} jetons "
            f"en {e.get('generation_elapsed_s')} s -> sandbox {e.get('isolation')} ({e.get('container_image')}) "
            f"code {e.get('exit_code')} en {e.get('elapsed_s')} s -> Judge {e.get('oracle_verdict')} "
            f"« {e.get('oracle_detail')} » -> Experience {x.get('id')} (fitness {x.get('fitness')})"
        )
    print(f"[first-loop] TRACE   TaskRun {detail.get('status')} / {detail.get('stop_reason')}")


def phase_run(api: Api) -> int:
    components = show_status(api)
    ollama_ready = components.get("ollama", {}).get("state") == "ready"
    sandbox_ready = components.get("sandbox", {}).get("state") == "ready"

    status, payload = api.call("POST", "/tasks/reference", {"kind": "is_palindrome"})
    task = payload.get("data") or {}
    proof(
        "1",
        "Task créée (tâche de référence is_palindrome, tests cachés)",
        status == 200 and bool(task.get("id")),
        f"HTTP {status}, tâche {task.get('id')}",
    )
    if not task.get("id"):
        return finish()

    started = time.monotonic()
    status, payload = api.call(
        "POST",
        f"/tasks/{task['id']}/runs",
        {
            "objective": "écrire is_palindrome",
            "max_attempts": 3,
            "max_candidates": 2,
            "max_wall_clock_s": 2400,
            "generation_timeout_s": 600,
        },
    )
    run = payload.get("data") or {}
    proof(
        "2",
        "TaskRun persistée « running » puis exécutée en arrière-plan",
        status == 202 and run.get("status") == "running",
        f"HTTP {status}, TaskRun {run.get('id')}",
    )
    if status != 202:
        print(json.dumps(payload, ensure_ascii=False)[:2000])
        return finish()

    detail = wait_terminal(api, run["id"])
    elapsed = time.monotonic() - started
    print(f"[first-loop] juste après l'exécution (modèle encore chargé) : {host_facts()}", flush=True)
    if detail.get("id"):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        RUN_FILE.write_text(
            json.dumps({**detail, "_apex_pid": api.pid}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    print(
        f"[first-loop] TaskRun : {detail.get('status')} / {detail.get('stop_reason')} après {elapsed:.0f} s"
    )
    if detail.get("failure"):
        print(f"[first-loop] échec rapporté : {str(detail['failure'])[:800]}")

    eval_runs = detail.get("eval_runs") or []
    experiences = detail.get("experiences") or []
    for i, attempt in enumerate(detail.get("attempts") or [], 1):
        print(
            f"[first-loop] tentative {i} : modèle {attempt.get('provider_name')} ; sandbox {attempt.get('sandbox_level')} ; "
            f"verdict {attempt.get('judge_result')} ; catégorie {attempt.get('category')} ; {attempt.get('duration_s')} s"
        )
    for i, e in enumerate(eval_runs, 1):
        print(
            f"[first-loop] --- EvalRun {i} ({e.get('id')}) : modèle {e.get('model_id')}, "
            f"{e.get('tokens_completion')} jetons générés ; appel au modèle {e.get('generation_elapsed_s')} s ; "
            f"durées rapportées par Ollama (s) : {e.get('backend_timings_s')}"
        )
        print("[first-loop] code RÉELLEMENT généré par le modèle :")
        for line in str(e.get("generated_code") or "").splitlines()[:60]:
            print(f"    | {line}")
        print(
            f"[first-loop] exécution : {e.get('sandbox_status')} (code {e.get('exit_code')}, {e.get('elapsed_s')} s) "
            f"isolation {e.get('isolation')} image {e.get('container_image')}"
        )
        print(f"[first-loop] {e.get('isolation_detail')}")
        print(f"[first-loop] stdout : {str(e.get('stdout') or '')[:600]!r}")
        print(
            f"[first-loop] verdict de l'Oracle : {e.get('oracle_verdict')} — {str(e.get('oracle_detail') or '')[:400]}"
        )
    for x in experiences:
        print(
            f"[first-loop] Experience {x.get('id')} : verdict {x.get('oracle_verdict')}, fitness {x.get('fitness')}, "
            f"échec {x.get('failure_category')}, empreinte {str(x.get('solution_fingerprint'))[:16]}…"
        )

    unfinished = detail.get("status") in (None, "running")
    if not eval_runs:
        # Aucune tentative jugée : sans modèle joignable, ou sans fin dans le délai sur ce matériel, les étapes
        # suivantes n'ont pas pu être exercées (BLOCKED, jamais PASS) ; une boucle TERMINÉE sans tentative
        # alors qu'un modèle était joignable est un défaut de la boucle (FAIL).
        reason = (
            f"TaskRun {detail.get('status')} : {str(detail.get('failure'))[:300]} ; "
            f"Ollama {components.get('ollama', {}).get('detail')}"
        )
        for number, label in (
            ("3", "modèle réel sélectionné par le routage"),
            ("4", "génération réelle"),
            ("5", "exécution réelle en sandbox"),
            ("6", "résultat capturé"),
            ("7", "évaluation réelle"),
            ("8", "Experience enregistrée"),
        ):
            proof(number, label, False if ollama_ready and not unfinished else None, reason)
        return finish()
    models = [e.get("model_id") for e in eval_runs if e.get("model_id")]
    proof(
        "3",
        "modèle réel sélectionné par le routage (Ollama, sonde physique)",
        bool(models) and ollama_ready,
        f"modèles essayés : {detail.get('providers_tried')} ; Ollama {components.get('ollama', {}).get('detail')}",
    )
    generated = [e for e in eval_runs if (e.get("generated_code") or "").strip()]
    proof(
        "4",
        "génération réelle (code produit par le modèle, enregistré)",
        bool(generated),
        f"{len(generated)} réponse(s) ; jetons : {[e.get('tokens_completion') for e in eval_runs]}",
    )
    executed = [
        e
        for e in eval_runs
        if e.get("sandbox_status") and e.get("isolation") in ("namespaced", "containerized", "hardened")
    ]
    proof(
        "5",
        "exécution réelle dans une sandbox isolée",
        bool(executed) if sandbox_ready else None,
        f"isolations : {[e.get('isolation') for e in eval_runs]} ; statuts : {[e.get('sandbox_status') for e in eval_runs]}",
    )
    proof(
        "6",
        "résultat capturé (sortie, code de sortie, durée)",
        bool(executed)
        and all(e.get("exit_code") is not None or e.get("sandbox_status") != "success" for e in executed),
        f"codes de sortie : {[e.get('exit_code') for e in eval_runs]}",
    )
    judged = [e for e in eval_runs if e.get("oracle_verdict") in ("pass", "fail")]
    proof(
        "7",
        "évaluation réelle (Oracle sur tests cachés, hors génération)",
        bool(judged) and len(judged) == len(eval_runs),
        f"verdicts : {[e.get('oracle_verdict') for e in eval_runs]} ; TaskRun {detail.get('status')}",
    )
    linked = {x.get("eval_run_id") for x in experiences} == {e.get("id") for e in eval_runs}
    print_trace(detail, eval_runs, experiences)
    proof(
        "8",
        "Experience enregistrée pour chaque tentative jugée (exécution ≠ apprentissage)",
        bool(experiences) and linked,
        f"{len(experiences)} Experience(s) pour {len(eval_runs)} EvalRun(s)",
    )
    return finish()


def phase_verify(api: Api) -> int:
    show_status(api)
    label9 = "persistance : TaskRun, résultat, code, verdicts et Experience relus après redémarrage"
    before = load_state(RUN_FILE, "9", label9)
    if before is None:
        return finish()
    if before.get("status") == "running":
        proof(
            "9",
            label9,
            None,
            "la TaskRun n'était pas terminée avant le redémarrage : rien de final à relire",
        )
        return finish()
    if before.get("_apex_pid") == api.pid:
        # Run 23 : `APEX.exe --stop` a échoué (Wsl/Service/0x8007274c), `--start` a constaté APEX déjà en
        # marche, et la relecture a eu lieu dans le MÊME processus — ce n'était pas une preuve de persistance.
        proof(
            "9",
            label9,
            None,
            f"APEX n'a pas été redémarré (même processus, pid {api.pid}) : rien de prouvé",
        )
        return finish()
    status, payload = api.call("GET", f"/task-runs/{before['id']}")
    after = payload.get("data") or {}
    same = all(
        after.get(k) == before.get(k)
        for k in ("status", "stop_reason", "eval_run_ids", "final_output", "attempts")
    )
    same_code = [e.get("generated_code") for e in after.get("eval_runs") or []] == [
        e.get("generated_code") for e in before.get("eval_runs") or []
    ]
    same_exp = sorted(x["id"] for x in after.get("experiences") or []) == sorted(
        x["id"] for x in before.get("experiences") or []
    )
    proof(
        "9",
        "persistance : TaskRun, résultat, code, verdicts et Experience relus après redémarrage",
        status == 200 and same and same_code and same_exp,
        f"APEX pid {before.get('_apex_pid')} -> {api.pid} ; TaskRun {before['id'][:8]} {after.get('status')} ; "
        f"identique : champs={same}, code={same_code}, experiences={same_exp}",
    )
    return finish()


def phase_interrupt(api: Api) -> int:
    before = load_state(RUN_FILE, "10", "reprise après redémarrage")
    if before is None:
        return finish()
    status, payload = api.call(
        "POST",
        f"/tasks/{before['task_id']}/runs",
        {
            "objective": "exécution interrompue volontairement par l'arrêt d'APEX",
            "max_attempts": 1,
            "generation_timeout_s": 600,
        },
    )
    run = payload.get("data") or {}
    INTERRUPT_FILE.write_text(json.dumps({"id": run.get("id"), "pid": api.pid}), encoding="utf-8")
    print(
        f"[first-loop] TaskRun {run.get('id')} démarrée (HTTP {status}, {run.get('status')}) — arrêt d'APEX demandé ensuite"
    )
    return 0 if status == 202 else 1


def phase_interrupted(api: Api) -> int:
    saved = load_state(INTERRUPT_FILE, "10", "reprise après redémarrage")
    if saved is None:
        return finish()
    status, payload = api.call("GET", f"/task-runs/{saved['id']}")
    run = payload.get("data") or {}
    print(
        f"[first-loop] après redémarrage (pid {saved['pid']} -> {api.pid}) : {run.get('status')} / "
        f"{run.get('stop_reason')} — {run.get('failure')}"
    )
    if run.get("status") in ("completed", "failed", "blocked"):
        proof(
            "10",
            "reprise après redémarrage : exécution coupée marquée « interrupted »",
            None,
            "la TaskRun s'est terminée avant l'arrêt — interruption non exercée",
        )
    else:
        proof(
            "10",
            "reprise après redémarrage : exécution coupée marquée « interrupted », jamais reprise en douce",
            status == 200
            and run.get("status") == "interrupted"
            and run.get("stop_reason") == "process_restarted"
            and api.pid != saved["pid"],
            f"statut {run.get('status')}",
        )
    return finish()


def main() -> int:
    phases = {
        "run": phase_run,
        "verify": phase_verify,
        "interrupt": phase_interrupt,
        "interrupted": phase_interrupted,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in phases:
        print(f"usage : {sys.argv[0]} {'|'.join(phases)}", file=sys.stderr)
        return 2
    return phases[sys.argv[1]](Api())


if __name__ == "__main__":
    sys.exit(main())
