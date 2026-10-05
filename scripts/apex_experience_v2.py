"""Protocole v2 (docs/PROTOCOLE_V2.md) : des faits structurés sur l'échec d'une tentative améliorent-ils
causalement la réussite de la tentative suivante ?

Ce script ne produit AUCUNE donnée de la boucle : il demande à l'APEX réellement démarré (vrai modèle, vraie
sandbox, vrai Judge) d'exécuter des TaskRuns, puis lit ce que la base a enregistré. Trois sous-commandes :

  calibrate  RUN 1 SEULS (T0, 1 tentative), 8 tirages jugés par tâche ; retient les tâches réussies 1 à 6 fois
             sur 8. N'exécute jamais de RUN 2.
  run        Expérience principale : RUN 1 (T0) sur une tâche neuve, tâches retenues en tourniquet fixe ; si le
             RUN 1 échoue en `incorrect_solution` ou `invalid_candidate`, bras A, B, C sur ce RUN 1, ordre en
             carré latin (ABC, BCA, CAB, tournant par instance éligible). Arrêt à 60 instances éligibles ou
             240 RUN 1 — jamais selon un RUN 2. Chaque bras est CONTRÔLÉ (température, stratégie, provenance,
             faits autorisés) : une violation arrête tout et invalide l'expérience.
  analyse    McNemar exact bilatéral C/A et B/A, correction de Holm, décision pré-enregistrée.

Usage (dans la distribution, avec le .venv d'APEX) :
  python scripts/apex_experience_v2.py calibrate --label primaire --out ~/apex-v2
  python scripts/apex_experience_v2.py run --label primaire --out ~/apex-v2
  python scripts/apex_experience_v2.py analyse --out ~/apex-v2
Codes : 0 phase menée à terme (quel que soit le résultat) ; 2 protocole violé ; 3 BLOQUÉE (APEX/modèle).

Machine cible (7B) : non validée au 05/10/2026 (UNVERIFIED, docs/PROTOCOLE_V2.md §0) — ne PAS lancer `run`
avec le label `primaire` tant que cette validation n'a pas été faite et rapportée.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import math
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

T0 = 0.7
DRAWS = 8
MAX_REDRAWS = 4
KEEP_MIN, KEEP_MAX = 1, 6
MAX_ELIGIBLE = 60
MAX_RUN1 = 240
MIN_DISCORDANT = 20
MAX_INFRA_SHARE = 0.10
ALPHA = 0.05
LATIN_SQUARE = (("A", "B", "C"), ("B", "C", "A"), ("C", "A", "B"))
ELIGIBLE_CATEGORIES = ("incorrect_solution", "invalid_candidate")
#: RUN 2 non jugé = erreur d'infrastructure (instance exclue des comparaisons, comptée et rapportée).
INFRA_CATEGORIES = ("infrastructure_failure", "generation_failure")
LABELS = ("primaire", "replication")


def _rule_hints() -> tuple[str, ...]:
    from apex.metalearner.adaptive_solution_loop import _INCORRECT_SOLUTION_HINT, _INVALID_CANDIDATE_HINT

    return (_INCORRECT_SOLUTION_HINT, _INVALID_CANDIDATE_HINT)


#: Les seuls textes que la règle (bras B) peut transmettre après un RUN 1 éligible (sans texte).
_RULE_HINTS = _rule_hints()


# -- Plan pré-enregistré (fonctions pures, testées) -------------------------------------------------------


def calibration_keeps(successes: int, judged: int) -> bool:
    return judged == DRAWS and KEEP_MIN <= successes <= KEEP_MAX


def arm_order(eligible_index: int) -> tuple[str, str, str]:
    return LATIN_SQUARE[eligible_index % len(LATIN_SQUARE)]


def task_for(run1_index: int, retained: list[str]) -> str:
    """Tourniquet FIXE sur les tâches retenues : ne dépend que du rang du RUN 1."""
    return retained[run1_index % len(retained)]


def should_stop(n_eligible: int, n_run1: int) -> bool:
    """Règle d'arrêt : ne lit QUE des RUN 1 (jamais un RUN 2)."""
    return n_eligible >= MAX_ELIGIBLE or n_run1 >= MAX_RUN1


def is_eligible(run1: dict) -> bool:
    return run1.get("failure_category") in ELIGIBLE_CATEGORIES


def is_infra(row: dict) -> bool:
    return not row.get("eval_run_id") or row.get("failure_category") in INFRA_CATEGORIES


def mcnemar_exact(gains: int, losses: int) -> float | None:
    """McNemar exact bilatéral (= test binomial sur les paires discordantes) ; None sans discordance."""
    n = gains + losses
    if n == 0:
        return None
    tail = sum(math.comb(n, i) for i in range(min(gains, losses) + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """p ajustés de Holm (step-down, monotones). Un test sans p (aucune discordance) compte dans m."""
    m = len(pvalues)
    known = sorted((p, k) for k, p in pvalues.items() if p is not None)
    adjusted: dict[str, float | None] = {k: None for k, p in pvalues.items() if p is None}
    running = 0.0
    for rank, (p, key) in enumerate(known):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[key] = running
    return adjusted


def decide(
    gains: int, losses: int, p_adjusted: float | None, *, infra_share: float, n_eligible: int, capped: bool
) -> str:
    if p_adjusted is not None and p_adjusted < ALPHA and gains > losses:
        return "amélioration mesurée"
    if p_adjusted is not None and p_adjusted < ALPHA and losses > gains:
        return "régression mesurée"
    insufficient = (
        gains + losses < MIN_DISCORDANT
        or infra_share > MAX_INFRA_SHARE
        or (capped and n_eligible < MAX_ELIGIBLE)
    )
    return "protocole insuffisant pour conclure" if insufficient else "aucune différence démontrée"


# -- Contrôles de validité de chaque bras ----------------------------------------------------------------


def arm_violations(
    arm: str, row: dict, run1: dict, *, hidden: list[str], expected_answer: str
) -> list[str]:
    """Ce qui rendrait la comparaison invalide. Vide : le bras est ce que le protocole déclare."""
    from apex.metalearner.failure_facts import (
        ALLOWED_FIELDS,
        PROMPT_VERSION,
        FailureFacts,
        render_failure_facts,
    )

    out = []
    generation = row.get("generation") or {}
    #: Sans Experience (aucune génération jugée : infrastructure), seuls les paramètres DEMANDÉS sont vérifiables.
    judged = row.get("experience_id") is not None
    if arm in ("RUN1", "A"):
        if row.get("condition_on_run") is not None:
            out.append("conditionné alors qu'il ne doit lire aucune expérience")
        if row.get("base_temperature") != T0 or (judged and generation != {"temperature": T0}):
            out.append(f"génération {generation} (attendu : température {T0} seule, aucun suffixe)")
    if arm in ("B", "C") and row.get("condition_on_run") != run1.get("run_id"):
        out.append("non conditionné sur le RUN 1 de l'instance")
    if arm in ("B", "C") and judged and row.get("source_experience_id") != run1.get("experience_id"):
        out.append("expérience source différente de celle du RUN 1")
    if arm == "B" and row.get("strategy") != "rule":
        out.append(f"stratégie {row.get('strategy')} (attendu : rule)")
    if arm == "B" and judged and generation.get("prompt_suffix") not in _RULE_HINTS:
        out.append("texte transmis par B différent des rappels constants de la règle")
    if arm == "C" and row.get("strategy") != "failure_facts":
        out.append(f"stratégie {row.get('strategy')} (attendu : failure_facts)")
    if arm == "C" and row.get("base_temperature") != T0:
        out.append(f"température demandée {row.get('base_temperature')} (attendu : {T0})")
    if arm == "C" and judged:
        facts = row.get("failure_facts")
        if generation.get("temperature") != T0:
            out.append(f"température {generation.get('temperature')} (attendu : {T0})")
        if row.get("prompt_version") != PROMPT_VERSION:
            out.append(f"gabarit {row.get('prompt_version')} (attendu : {PROMPT_VERSION})")
        if row.get("source_eval_run_id") != run1.get("eval_run_id"):
            out.append("faits tirés d'un autre EvalRun que celui du RUN 1")
        if not isinstance(facts, dict) or set(facts) != set(ALLOWED_FIELDS):
            out.append(f"champs transmis {sorted(facts or {})} (attendu : {sorted(ALLOWED_FIELDS)})")
        elif facts != run1.get("failure_facts"):
            out.append("faits transmis différents des faits du RUN 1 (relus indépendamment)")
        else:
            facts_obj = FailureFacts(**{**facts, "exception_classes": tuple(facts["exception_classes"])})
            if generation.get("prompt_suffix") != render_failure_facts(facts_obj):
                out.append("texte transmis différent du gabarit figé")
    suffix = generation.get("prompt_suffix") or ""
    leaks = [s for s in hidden if len(s) >= 4 and s in suffix]
    if leaks or (expected_answer and expected_answer in suffix):
        out.append(f"FUITE dans le texte transmis : {leaks or 'réponses attendues'}")
    return out


# -- Mesures ----------------------------------------------------------------------------------------------


def _api():
    """Même client que la preuve de la première boucle (backend.json + jeton du dépôt), jamais recopié."""
    path = (
        Path(__file__).resolve().parents[1]
        / "setup"
        / "packaging"
        / "windows"
        / "ci"
        / "first_real_loop.py"
    )
    spec = importlib.util.spec_from_file_location("first_real_loop", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_nvidia_smi(text: str) -> list[dict] | None:
    """`nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader,nounits`."""
    gpus = []
    for line in (text or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            name, driver, used, total = parts
            gpus.append(
                {"name": name, "driver": driver, "vram_used_mib": _int(used), "vram_total_mib": _int(total)}
            )
    return gpus or None


def parse_ollama_models(payload: Any) -> list[dict] | None:
    """`GET /api/ps` -> modèle, taille chargée, part en VRAM et en RAM telles que RAPPORTÉES par Ollama."""
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None
    out = []
    for m in models:
        size, vram = m.get("size"), m.get("size_vram")
        share = round(vram / size, 3) if isinstance(size, int) and isinstance(vram, int) and size else None
        out.append(
            {
                "name": m.get("name"),
                "size_bytes": size,
                "size_vram_bytes": vram,
                "vram_share": share,
                "ram_share": round(1 - share, 3) if share is not None else None,
            }
        )
    return out


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def machine_facts() -> dict:
    """CPU, RAM, GPU, VRAM, pilote, modèle chargé par Ollama : MESURÉS à l'instant, `None` si non mesurables —
    jamais une valeur supposée."""
    facts: dict[str, Any] = {
        "cpu": None,
        "cpu_count": None,
        "ram_total_mib": None,
        "ram_available_mib": None,
        "gpus": None,
        "ollama_models": None,
    }
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        names = [ln.split(":", 1)[1].strip() for ln in cpuinfo.splitlines() if ln.startswith("model name")]
        facts["cpu"], facts["cpu_count"] = (names[0] if names else None), (len(names) or None)
        mem = {
            ln.split(":")[0]: int(ln.split()[1]) // 1024
            for ln in Path("/proc/meminfo").read_text().splitlines()
            if ln.split(":")[0] in ("MemTotal", "MemAvailable")
        }
        facts["ram_total_mib"], facts["ram_available_mib"] = mem.get("MemTotal"), mem.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    for exe in ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi"):
        try:
            res = subprocess.run(  # noqa: S603 — exécutables fixes, arguments constants
                [
                    exe,
                    "--query-gpu=name,driver_version,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            facts["gpus"] = parse_nvidia_smi(res.stdout)
            break
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as response:
            facts["ollama_models"] = parse_ollama_models(json.loads(response.read()))
    except (OSError, ValueError):
        pass
    return facts


def run_once(
    api,
    task_id: str,
    *,
    timeout_s: float,
    condition_on_run: str | None = None,
    strategy: str | None = None,
    base_temperature: float | None = None,
) -> dict:
    body: dict[str, Any] = {
        "objective": "résoudre la tâche",
        "max_attempts": 1,
        "max_candidates": 1,
        "max_wall_clock_s": timeout_s,
        "generation_timeout_s": min(600.0, timeout_s),
    }
    if condition_on_run:
        body["condition_on_run"] = condition_on_run
    if strategy:
        body["strategy"] = strategy
    if base_temperature is not None:
        body["base_temperature"] = base_temperature
    status, payload = api.call("POST", f"/tasks/{task_id}/runs", body)
    if status != 202:
        raise RuntimeError(
            f"TaskRun refusée (HTTP {status}) : {json.dumps(payload, ensure_ascii=False)[:400]}"
        )
    run_id = payload["data"]["id"]
    deadline = time.monotonic() + timeout_s + 120
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            status, payload = api.call("GET", f"/task-runs/{run_id}", timeout_s=120)
        except OSError:
            continue
        detail = payload.get("data") or {}
        if detail.get("status") not in (None, "running"):
            return detail
    return {"id": run_id, "status": "running", "eval_runs": [], "experiences": []}


def record(detail: dict, *, kind: str, arm: str, instance: int | None, label: str) -> dict:
    """Une ligne par TaskRun : tout ce que la base a enregistré, rien d'estimé ni de reconstruit."""
    eval_run = (detail.get("eval_runs") or [{}])[0]
    experience = (detail.get("experiences") or [{}])[0]
    config = experience.get("generation_config") or {}
    attempt = (detail.get("attempts") or [{}])[0]
    return {
        "label": label,
        "kind": kind,
        "arm": arm,
        "instance": instance,
        "task_id": detail.get("task_id"),
        "run_id": detail.get("id"),
        "status": detail.get("status"),
        "stop_reason": detail.get("stop_reason"),
        "model": eval_run.get("model_id"),
        "condition_on_run": detail.get("condition_on_run"),
        "strategy": detail.get("strategy"),
        "base_temperature": detail.get("base_temperature"),
        "generation": config.get("generation"),
        "prompt_version": config.get("prompt_version"),
        "failure_facts": config.get("failure_facts"),
        "source_eval_run_id": config.get("source_eval_run_id"),
        "source_experience_id": experience.get("parent_experience_id"),
        "strategy_reason": experience.get("mutation_description"),
        "judge_result": attempt.get("judge_result"),
        "success": attempt.get("judge_result") == "pass",
        "eval_run_id": eval_run.get("id"),
        "oracle_verdict": eval_run.get("oracle_verdict"),
        "oracle_detail": eval_run.get("oracle_detail"),
        "failure_category": eval_run.get("failure_category"),
        # Faits de CET EvalRun relus par l'API (contrôle croisé du bras C qui suit) ; pas un texte transmis.
        "own_failure_facts": eval_run.get("failure_facts"),
        "exit_code": eval_run.get("exit_code"),
        "sandbox": eval_run.get("isolation"),
        "generation_s": eval_run.get("generation_elapsed_s"),
        "backend_timings_s": eval_run.get("backend_timings_s"),
        "tokens": eval_run.get("tokens_completion"),
        "fingerprint": experience.get("solution_fingerprint"),
        "experience_id": experience.get("id"),
        "experience_category": experience.get("failure_category"),
        "generated_code": eval_run.get("generated_code"),
    }


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _new_task(api, kind: str) -> str:
    status, payload = api.call("POST", "/tasks/reference", {"kind": kind})
    task = payload.get("data") or {}
    if status != 200 or not task.get("id"):
        raise LookupError(f"tâche « {kind} » non créée (HTTP {status})")
    return task["id"]


def _hidden(kind: str) -> tuple[list[str], str]:
    from apex.taskgen.solution_tasks import _CONTROLLED_TASKS, build_reference_task_spec

    spec = build_reference_task_spec(kind)
    public = (spec.description, *_RULE_HINTS)
    if kind in _CONTROLLED_TASKS:
        inputs = [repr(a) for args, _ in _CONTROLLED_TASKS[kind][2] for a in args]
        inputs += [a for args, _ in _CONTROLLED_TASKS[kind][2] for a in args if isinstance(a, str)]
    else:
        line = next(ln for ln in spec.solution_harness.splitlines() if ln.startswith("_APEX_INPUTS = "))
        inputs = [str(x) for x in ast.literal_eval(line.split(" = ", 1)[1])]
    # Une chaîne déjà PUBLIQUE (énoncé, rappels constants de la règle B) n'est pas un secret : « Python » est à la
    # fois une entrée cachée de `count_vowels` et un mot de chaque énoncé.
    return [i for i in inputs if not any(i in text for text in public)], spec.expected_answer or ""


def _print(row: dict) -> None:
    gen = row.get("generation") or {}
    print(
        f"[v2] {row['kind']} {row['arm']:4} inst {row['instance']} run {str(row['run_id'])[:8]} {row['model']} "
        f"T={gen.get('temperature')} texte={'oui' if gen.get('prompt_suffix') else 'non'} -> "
        f"{row['judge_result']} ({row['failure_category']}) {row['oracle_detail']} ; {row['generation_s']} s ; "
        f"Ollama {row['backend_timings_s']}",
        flush=True,
    )


# -- Phases ----------------------------------------------------------------------------------------------


def calibrate(api, out: Path, *, label: str, kinds: list[str], timeout_s: float) -> int:
    path = out / "calibration.jsonl"
    rows = _rows(path)
    for kind in kinds:
        mine = [r for r in rows if r["kind"] == kind]
        while sum(1 for r in mine if not is_infra(r)) < DRAWS and len(mine) < DRAWS + MAX_REDRAWS:
            row = record(
                run_once(api, _new_task(api, kind), timeout_s=timeout_s, base_temperature=T0),
                kind=kind,
                arm="RUN1",
                instance=None,
                label=label,
            )
            if bad := arm_violations("RUN1", row, {}, hidden=[], expected_answer=""):
                print(f"[v2] PROTOCOLE VIOLÉ (calibration {kind}) : {bad}")
                return 2
            _append(path, row)
            rows.append(row)
            mine.append(row)
            _print(row)
    models = sorted({r["model"] for r in rows if r.get("model")})
    per_task = {}
    for kind in kinds:
        judged = [r for r in rows if r["kind"] == kind and not is_infra(r)][:DRAWS]
        successes = sum(r["success"] for r in judged)
        per_task[kind] = {
            "judged": len(judged),
            "successes": successes,
            "infra": sum(1 for r in rows if r["kind"] == kind and is_infra(r)),
            "retained": calibration_keeps(successes, len(judged)),
        }
    summary = {
        "label": label,
        "models": models,
        "T0": T0,
        "draws": DRAWS,
        "keep": [KEEP_MIN, KEEP_MAX],
        "tasks": per_task,
        "retained": [k for k in kinds if per_task[k]["retained"]],
        "machine": machine_facts(),
    }
    (out / "calibration.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for kind, t in per_task.items():
        print(
            f"[v2] calibration {kind:16} {t['successes']}/{t['judged']} (infra {t['infra']}) "
            f"{'RETENUE' if t['retained'] else 'écartée'}"
        )
    print(f"[v2] modèle(s) : {models} ; tâches retenues : {summary['retained']}")
    if not models:
        print("[v2] BLOCKED : aucune génération réelle (modèle indisponible ?)")
        return 3
    return 0 if len(models) == 1 else 2


def main_experiment(api, out: Path, *, label: str, timeout_s: float) -> int:
    calibration = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
    if calibration.get("label") != label or len(calibration.get("models") or []) != 1:
        print(
            f"[v2] BLOCKED : calibration de label {calibration.get('label')} / modèles {calibration.get('models')}"
        )
        return 2
    retained, model = calibration["retained"], calibration["models"][0]
    if not retained:
        print(
            "[v2] BLOCKED : aucune tâche retenue par la calibration — protocole insuffisant, rien à lancer"
        )
        return 3
    path = out / "runs.jsonl"
    rows = _rows(path)
    if not rows:
        _append(
            path,
            {
                "arm": "META",
                "label": label,
                "model": model,
                "retained": retained,
                "T0": T0,
                "machine_start": machine_facts(),
            },
        )
        rows = _rows(path)
    run1s = [r for r in rows if r.get("arm") == "RUN1"]
    while True:
        eligible = [r for r in run1s if is_eligible(r)]
        last = run1s[-1] if run1s else None
        if last is not None and is_eligible(last):  # instance (éventuellement interrompue) à compléter
            index = len(eligible) - 1
            done = {
                r["arm"] for r in rows if r.get("instance") == index and r.get("arm") in ("A", "B", "C")
            }
            hidden, expected = _hidden(last["kind"])
            for arm in [a for a in arm_order(index) if a not in done]:
                kwargs = {
                    "A": {"base_temperature": T0},
                    "B": {"condition_on_run": last["run_id"], "strategy": "rule"},
                    "C": {
                        "condition_on_run": last["run_id"],
                        "strategy": "failure_facts",
                        "base_temperature": T0,
                    },
                }[arm]
                row = record(
                    run_once(api, last["task_id"], timeout_s=timeout_s, **kwargs),
                    kind=last["kind"],
                    arm=arm,
                    instance=index,
                    label=label,
                )
                run1_view = {**last, "failure_facts": last.get("own_failure_facts")}
                row["violations"] = arm_violations(
                    arm, row, run1_view, hidden=hidden, expected_answer=expected
                )
                row["machine"] = machine_facts()
                _append(path, row)
                rows.append(row)
                _print(row)
                if row["violations"]:
                    print(
                        f"[v2] PROTOCOLE VIOLÉ (instance {index}, bras {arm}) : {row['violations']} — arrêt"
                    )
                    return 2
        if should_stop(len(eligible), len(run1s)):
            break
        kind = task_for(len(run1s), retained)
        row = record(
            run_once(api, _new_task(api, kind), timeout_s=timeout_s, base_temperature=T0),
            kind=kind,
            arm="RUN1",
            instance=None,
            label=label,
        )
        row["violations"] = arm_violations("RUN1", row, {}, hidden=[], expected_answer="")
        if row.get("model") and row["model"] != model:
            row["violations"].append(f"modèle {row['model']} différent de la calibration ({model})")
        if is_eligible(row):
            row["instance"] = len(eligible)
        _append(path, row)
        rows.append(row)
        run1s.append(row)
        _print(row)
        if row["violations"]:
            print(f"[v2] PROTOCOLE VIOLÉ (RUN 1) : {row['violations']} — arrêt")
            return 2
    _append(
        path,
        {
            "arm": "END",
            "machine_end": machine_facts(),
            "n_run1": len(run1s),
            "n_eligible": len([r for r in run1s if is_eligible(r)]),
        },
    )
    print(
        f"[v2] terminé : {len(run1s)} RUN 1, {len([r for r in run1s if is_eligible(r)])} instances éligibles"
    )
    return 0


def analyse(rows: list[dict]) -> dict:
    meta = [r for r in rows if r.get("arm") == "META"]
    run_rows = [r for r in rows if r.get("arm") in ("RUN1", "A", "B", "C")]
    models = sorted({r["model"] for r in run_rows if r.get("model")})
    labels = sorted({r["label"] for r in run_rows if r.get("label")})
    violations = [r for r in run_rows if r.get("violations")]
    run1s = [r for r in run_rows if r["arm"] == "RUN1"]
    eligible = [r for r in run1s if is_eligible(r)]
    capped = len(run1s) >= MAX_RUN1
    by_instance: dict[int, dict[str, dict]] = {}
    for r in run_rows:
        if r["arm"] in ("A", "B", "C") and r.get("instance") is not None:
            by_instance.setdefault(r["instance"], {})[r["arm"]] = r
    complete, infra_excluded, incomplete = [], [], []
    for index in range(len(eligible)):
        arms = by_instance.get(index, {})
        if set(arms) != {"A", "B", "C"}:
            incomplete.append(index)
        elif any(is_infra(arms[a]) for a in "ABC"):
            infra_excluded.append(index)
        else:
            complete.append(arms)
    infra_share = len(infra_excluded) / len(eligible) if eligible else 0.0
    comparisons: dict[str, dict] = {}
    for arm in ("C", "B"):
        gains = sum(1 for i in complete if i[arm]["success"] and not i["A"]["success"])
        losses = sum(1 for i in complete if not i[arm]["success"] and i["A"]["success"])
        comparisons[f"{arm}_vs_A"] = {
            "pairs": len(complete),
            "discordant": gains + losses,
            "concordant": len(complete) - gains - losses,
            "gains": gains,
            "losses": losses,
            "successes_A": sum(i["A"]["success"] for i in complete),
            f"successes_{arm}": sum(i[arm]["success"] for i in complete),
            "difference": (
                sum(i[arm]["success"] for i in complete) - sum(i["A"]["success"] for i in complete)
            ),
            "p": mcnemar_exact(gains, losses),
        }
    adjusted = holm({k: v["p"] for k, v in comparisons.items()})
    for key, comp in comparisons.items():
        comp["p_holm"] = adjusted[key]
        comp["correction"] = "Holm, 2 tests, alpha global 0,05"
        comp["decision"] = decide(
            comp["gains"],
            comp["losses"],
            adjusted[key],
            infra_share=infra_share,
            n_eligible=len(eligible),
            capped=capped,
        )
    invalid = []
    if violations:
        invalid.append(f"{len(violations)} TaskRun(s) hors protocole")
    if len(models) > 1 or len(labels) > 1:
        invalid.append(f"modèles {models} / labels {labels} mélangés")
    if incomplete:
        invalid.append(f"instances incomplètes {incomplete}")
    if not should_stop(len(eligible), len(run1s)):
        invalid.append("règle d'arrêt non atteinte (expérience inachevée)")
    if invalid:
        for comp in comparisons.values():
            comp["decision"] = "protocole insuffisant pour conclure"
    return {
        "label": labels,
        "models": models,
        "meta": meta[0] if meta else None,
        "n_run1": len(run1s),
        "n_eligible": len(eligible),
        "n_complete": len(complete),
        "infra_excluded": infra_excluded,
        "infra_share": round(infra_share, 3),
        "capped_at_240_run1": capped,
        "invalid": invalid,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("phase", choices=("calibrate", "run", "analyse"))
    parser.add_argument("--label", choices=LABELS, default="replication")
    parser.add_argument("--kinds", default=None, help="calibration : tâches (défaut : les 15)")
    parser.add_argument("--timeout-s", type=float, default=900.0, help="par TaskRun")
    parser.add_argument("--out", default=str(Path.home() / "apex-v2"))
    parser.add_argument(
        "--target-validated",
        default=None,
        metavar="RAPPORT",
        help="run --label primaire : rapport de la validation machine cible (exigé)",
    )
    args = parser.parse_args()
    if (
        args.phase == "run"
        and args.label == "primaire"
        and not (args.target_validated and Path(args.target_validated).expanduser().is_file())
    ):
        print(
            "[v2] REFUSÉ : l'expérience primaire exige la validation préalable de la machine cible "
            "(--target-validated <rapport>) — docs/PROTOCOLE_V2.md §0"
        )
        return 2
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if args.phase == "analyse":
        summary = analyse(_rows(out / "runs.jsonl"))
        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in summary.items() if k != "meta"}, ensure_ascii=False, indent=1))
        return 0
    from apex.taskgen.solution_tasks import REFERENCE_TASK_KINDS

    module = _api()
    api = module.Api()
    print(f"[v2] APEX pid {api.pid} ; {json.dumps(machine_facts(), ensure_ascii=False)}", flush=True)
    try:
        if args.phase == "calibrate":
            kinds = [
                k.strip() for k in (args.kinds or ",".join(REFERENCE_TASK_KINDS)).split(",") if k.strip()
            ]
            return calibrate(api, out, label=args.label, kinds=kinds, timeout_s=args.timeout_s)
        return main_experiment(api, out, label=args.label, timeout_s=args.timeout_s)
    except (LookupError, RuntimeError) as exc:
        print(f"[v2] BLOCKED : {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
