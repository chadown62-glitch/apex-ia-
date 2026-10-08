#!/usr/bin/env python3
"""Rejoue un job des expériences « cycle cognitif » (validation/cognitive_jobs.json) EXACTEMENT comme mesuré en local :
chaque étape s'exécute depuis un worktree git au commit FIGÉ de l'expérience (le code mesuré ne change pas), avec,
si l'étape le dit (`harness_from`), le harnais et les bancs d'un autre commit. Le modèle est celui de
l'environnement (`APEX_MODELS_DIR`) : poids substituts en local, Qwen2.5-Coder-1.5B-Instruct en CI.

  run_cognitive_job.py --list [--set primary|replication]   noms des jobs (matrice CI)
  run_cognitive_job.py <job> --out-dir DIR [--work DIR] [--dry-run]

Aucune donnée n'est produite ici : le harnais (`scripts/apex_real_validation.py`) écrit ses résultats bruts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "validation" / "cognitive_jobs.json"
HARNESS_FILES = ("scripts/apex_real_validation.py", "scripts/apex_bench_tasks.py")


def load_jobs() -> dict[str, dict]:
    return json.loads(JOBS.read_text(encoding="utf-8"))["jobs"]


def worktree(work: Path, commit: str, harness_from: str | None, *, dry_run: bool) -> Path:
    path = work / "worktrees" / (commit + (f"-h{harness_from}" if harness_from else ""))
    if dry_run or path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(path), commit], check=True)
    if harness_from:
        for rel in HARNESS_FILES:
            shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{harness_from}:{rel}"],
                                   capture_output=True, text=True)
            if shown.returncode == 0:
                (path / rel).write_text(shown.stdout, encoding="utf-8")
    return path


def commands(job: dict, *, out_dir: Path, work: Path, dry_run: bool) -> list[tuple[Path, list[str], dict[str, str]]]:
    out = []
    for step in job["steps"]:
        wt = worktree(work, step["commit"], step.get("harness_from"), dry_run=dry_run)
        args = [a.replace("{work}", str(work)) for a in step["args"]]
        cmd = [sys.executable, str(wt / "scripts" / "apex_real_validation.py"), step["phase"], *args,
               "--out", str(out_dir / step["out"])]
        env = {"PYTHONPATH": str(wt), "APEX_COMMIT": step["commit"]}
        out.append((wt, cmd, env))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job", nargs="?")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--set", default="primary", choices=("primary", "replication"))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--work", default=None, help="worktrees et bases d'entraînement (défaut : <out-dir>/.work)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    jobs = load_jobs()
    if args.list:
        print(json.dumps([name for name, job in jobs.items() if job["set"] == args.set]))
        return 0
    if args.job not in jobs:
        raise SystemExit(f"job inconnu : {args.job!r}")
    assert args.out_dir, "--out-dir requis"
    out_dir = Path(args.out_dir).resolve()
    work = Path(args.work).resolve() if args.work else out_dir / ".work"
    out_dir.mkdir(parents=True, exist_ok=True)
    for wt, cmd, env in commands(jobs[args.job], out_dir=out_dir, work=work, dry_run=args.dry_run):
        if Path(cmd[-1]).exists():
            print(f"déjà présent, étape ignorée : {cmd[-1]}", flush=True)
            continue
        print(" ".join(cmd), flush=True)
        if not args.dry_run:
            subprocess.run(cmd, cwd=wt, env={**os.environ, **env}, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
