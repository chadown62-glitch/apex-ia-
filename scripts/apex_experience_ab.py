"""Expérience RUN 1 -> Experience -> RUN 2 : l'expérience du RUN 1 améliore-t-elle le RUN 2 ?

Protocole pré-enregistré : docs/EXPERIENCE_RUN1_RUN2.md. Ce script ne produit AUCUNE donnée de la boucle : il
demande à l'APEX réellement démarré (vrai modèle, vraie sandbox, vrai Judge) d'exécuter des TaskRuns, puis lit
ce que la base a enregistré. Il est fait pour un APEX en service (Panel « Démarrer APEX », ou CI).

Pour chaque tâche contrôlée et chaque réplique :
  RUN 1                 1 tentative, aucune expérience lue ;
  RUN 2 « baseline »    1 tentative, aucune expérience lue (même stratégie que RUN 1) ;
  RUN 2 « conditionné » 1 tentative, `condition_on_run` = RUN 1 (règle existante decide_strategy_from_experiences).
L'ordre des deux RUN 2 alterne d'une réplique à l'autre. Analyse primaire : paires dont le RUN 1 a échoué.

Usage (dans la distribution, avec le .venv d'APEX) :
  python scripts/apex_experience_ab.py --replicates 4 --out ~/apex-experience-ab
Codes : 0 expérience menée à terme (quel que soit son résultat) ; 3 BLOQUÉE (APEX/modèle indisponible).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import time
from pathlib import Path

KINDS = ("is_palindrome", "rle_encode", "count_vowels", "word_count", "second_largest")
_INCORRECT = re.compile(r"^(\d+)/(\d+) cas incorrects")
_CORRECT = re.compile(r"^(\d+)/(\d+) résultats conformes")


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
    return module.Api(), module.host_facts


def score(oracle_verdict: str | None, oracle_detail: str | None) -> tuple[int | None, int | None]:
    """(cas réussis, cas au total) tels que RAPPORTÉS par l'Oracle ; (0, None) si aucun résultat n'a été publié
    (le script candidat n'a rien produit) ; (None, None) si rien n'a été jugé."""
    detail = oracle_detail or ""
    if m := _CORRECT.match(detail):
        return int(m.group(1)), int(m.group(2))
    if m := _INCORRECT.match(detail):
        return int(m.group(2)) - int(m.group(1)), int(m.group(2))
    if oracle_verdict in ("fail", "error"):
        return 0, None
    return None, None


def run_once(api, task_id: str, *, condition_on_run: str | None, timeout_s: float) -> dict:
    body = {
        "objective": "résoudre la tâche",
        "max_attempts": 1,
        "max_candidates": 1,
        "max_wall_clock_s": timeout_s,
        "generation_timeout_s": min(600.0, timeout_s),
    }
    if condition_on_run:
        body["condition_on_run"] = condition_on_run
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


def record(kind: str, replicate: int, arm: str, detail: dict) -> dict:
    """Une ligne par TaskRun : tout ce que la base a enregistré, rien d'estimé."""
    eval_run = (detail.get("eval_runs") or [{}])[0]
    experience = (detail.get("experiences") or [{}])[0]
    generation = (experience.get("generation_config") or {}).get("generation") or {}
    passed, total = score(eval_run.get("oracle_verdict"), eval_run.get("oracle_detail"))
    return {
        "kind": kind,
        "replicate": replicate,
        "arm": arm,
        "task_id": detail.get("task_id"),
        "run_id": detail.get("id"),
        "status": detail.get("status"),
        "stop_reason": detail.get("stop_reason"),
        "condition_on_run": detail.get("condition_on_run"),
        "model": eval_run.get("model_id"),
        "strategy": {
            "temperature": generation.get("temperature"),
            "prompt_suffix": generation.get("prompt_suffix"),
        },
        "source_experience_id": experience.get("parent_experience_id"),
        "strategy_reason": experience.get("mutation_description"),
        "oracle_verdict": eval_run.get("oracle_verdict"),
        "oracle_detail": eval_run.get("oracle_detail"),
        "cases_passed": passed,
        "cases_total": total,
        "exit_code": eval_run.get("exit_code"),
        "sandbox": eval_run.get("isolation"),
        "generation_s": eval_run.get("generation_elapsed_s"),
        "tokens": eval_run.get("tokens_completion"),
        "generated_code": eval_run.get("generated_code"),
        "fingerprint": experience.get("solution_fingerprint"),
        "experience_id": experience.get("id"),
        "fitness": experience.get("fitness"),
        "failure_category": experience.get("failure_category"),
    }


def sign_test_p(wins: int, losses: int) -> float | None:
    """Test du signe exact, bilatéral (ex aequo exclus). None sans paire discordante."""
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def analyse(rows: list[dict]) -> dict:
    by_key: dict[tuple, dict[str, dict]] = {}
    for row in rows:
        by_key.setdefault((row["kind"], row["replicate"]), {})[row["arm"]] = row
    pairs, excluded = [], []
    for (kind, replicate), arms in sorted(by_key.items()):
        run1, base, cond = arms.get("run1"), arms.get("baseline"), arms.get("conditioned")
        if not (run1 and base and cond) or None in (base["cases_passed"], cond["cases_passed"]):
            excluded.append(
                {"kind": kind, "replicate": replicate, "reason": "TaskRun manquante ou non jugée"}
            )
            continue
        if run1["oracle_verdict"] == "pass":
            excluded.append(
                {"kind": kind, "replicate": replicate, "reason": "RUN 1 réussi : aucun échec à exploiter"}
            )
            continue
        if not cond["source_experience_id"]:
            excluded.append(
                {
                    "kind": kind,
                    "replicate": replicate,
                    "reason": "RUN 2 conditionné sans expérience source (stratégie de repli)",
                }
            )
            continue
        pairs.append(
            {
                "kind": kind,
                "replicate": replicate,
                "baseline": base["cases_passed"],
                "conditioned": cond["cases_passed"],
                "baseline_pass": base["oracle_verdict"] == "pass",
                "conditioned_pass": cond["oracle_verdict"] == "pass",
                "same_code": base["fingerprint"] == cond["fingerprint"],
            }
        )
    wins = sum(1 for p in pairs if p["conditioned"] > p["baseline"])
    losses = sum(1 for p in pairs if p["conditioned"] < p["baseline"])
    ties = len(pairs) - wins - losses
    p_value = sign_test_p(wins, losses)
    if p_value is not None and p_value < 0.05 and wins > losses:
        conclusion = "AMÉLIORATION mesurée (test du signe, p < 0,05)"
    elif p_value is not None and p_value < 0.05 and losses > wins:
        conclusion = "RÉGRESSION mesurée (test du signe, p < 0,05)"
    else:
        conclusion = "AUCUNE différence démontrée (p >= 0,05 ou aucune paire discordante)"
    return {
        "pairs": pairs,
        "excluded": excluded,
        "n_pairs": len(pairs),
        "conditioned_better": wins,
        "conditioned_worse": losses,
        "ties": ties,
        "sign_test_p": p_value,
        "pass_baseline": sum(p["baseline_pass"] for p in pairs),
        "pass_conditioned": sum(p["conditioned_pass"] for p in pairs),
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--replicates", type=int, default=4)
    parser.add_argument("--kinds", default=",".join(KINDS))
    parser.add_argument("--timeout-s", type=float, default=900.0, help="par TaskRun")
    parser.add_argument("--out", default=str(Path.home() / "apex-experience-ab"))
    args = parser.parse_args()

    api, host_facts = _api()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    runs_file = out / "runs.jsonl"
    rows: list[dict] = []
    started = time.monotonic()
    print(f"[ab] APEX pid {api.pid} ; {host_facts()} ; résultats : {runs_file}", flush=True)
    for kind in [k.strip() for k in args.kinds.split(",") if k.strip()]:
        for replicate in range(1, args.replicates + 1):
            status, payload = api.call("POST", "/tasks/reference", {"kind": kind})
            task = payload.get("data") or {}
            if status != 200 or not task.get("id"):
                print(f"[ab] BLOCKED : tâche « {kind} » non créée (HTTP {status})")
                return 3
            run1 = run_once(api, task["id"], condition_on_run=None, timeout_s=args.timeout_s)
            order = ("baseline", "conditioned") if replicate % 2 else ("conditioned", "baseline")
            details = {"run1": run1}
            for arm in order:
                details[arm] = run_once(
                    api,
                    task["id"],
                    timeout_s=args.timeout_s,
                    condition_on_run=run1.get("id") if arm == "conditioned" else None,
                )
            for arm in ("run1", "baseline", "conditioned"):
                row = record(kind, replicate, arm, details[arm])
                rows.append(row)
                with runs_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    f"[ab] {kind} r{replicate} {arm:11} run {str(row['run_id'])[:8]} {row['model']} "
                    f"T={row['strategy']['temperature']} indice={'oui' if row['strategy']['prompt_suffix'] else 'non'} "
                    f"-> {row['oracle_verdict']} {row['cases_passed']}/{row['cases_total']} "
                    f"({row['generation_s']} s) exp {str(row['experience_id'])[:8]} "
                    f"source {str(row['source_experience_id'])[:8]}",
                    flush=True,
                )
            if all(r["status"] == "error" for r in rows[-3:]) or not any(r["model"] for r in rows[-3:]):
                print("[ab] BLOCKED : aucune génération réelle (modèle indisponible ?) — arrêt")
                return 3
    summary = analyse(rows)
    summary["duration_s"] = round(time.monotonic() - started)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ab] paires analysées : {summary['n_pairs']} (exclues : {len(summary['excluded'])})")
    for item in summary["excluded"]:
        print(f"[ab]   exclue {item['kind']} r{item['replicate']} : {item['reason']}")
    for p in summary["pairs"]:
        print(
            f"[ab]   {p['kind']} r{p['replicate']} : baseline {p['baseline']} cas, conditionné {p['conditioned']} cas"
            f"{' (code identique)' if p['same_code'] else ''}"
        )
    print(
        f"[ab] conditionné meilleur {summary['conditioned_better']}, pire {summary['conditioned_worse']}, "
        f"égal {summary['ties']} ; p (signe) = {summary['sign_test_p']} ; "
        f"réussites baseline {summary['pass_baseline']} / conditionné {summary['pass_conditioned']}"
    )
    print(f"[ab] CONCLUSION : {summary['conclusion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
