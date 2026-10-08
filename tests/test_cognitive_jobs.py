"""Les expériences « cycle cognitif » restent RELANÇABLES à l'identique (docs/VALIDATION_REELLE.md §6) : chaque job
(`validation/cognitive_jobs.json`) désigne le commit figé mesuré, le harnais utilisé et les arguments pré-enregistrés ;
l'exécuteur (`scripts/run_cognitive_job.py`) les rejoue depuis un worktree. Aucun modèle ici."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_cognitive_job", ROOT / "scripts" / "run_cognitive_job.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)
JOBS = runner.load_jobs()


def test_every_protocol_has_its_primary_seeds_and_a_replication_twin() -> None:
    for protocol in ("6.1", "6.2", "6.3", "6.4", "6.5"):
        primary = [n for n, j in JOBS.items() if j["protocol"] == protocol and j["set"] == "primary"]
        assert primary, protocol
        for name in primary:
            assert name.endswith(("-s0", "-s1", "-s2", "-s3"))
            twin = name.rsplit("-s", 1)[0] + "-s1" + name.rsplit("-s", 1)[1]  # s0 -> s10 … s3 -> s13
            assert twin in JOBS and JOBS[twin]["set"] == "replication", name
    request = json.loads((ROOT / "validation" / "request-cognitive.json").read_text(encoding="utf-8"))
    assert {j["name"] for j in request["jobs"]} == {n for n, j in JOBS.items() if j["set"] == "primary"}
    assert "Coder" in request["model"]  # la CI rejoue avec la variante Coder, jamais avec les poids substituts


def _shallow() -> bool:
    out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"], capture_output=True, text=True)
    return out.stdout.strip() != "false"


@pytest.mark.skipif(_shallow(), reason="clone superficiel : les commits figés ne sont pas présents")
def test_every_frozen_commit_exists() -> None:
    commits = {s[k] for j in JOBS.values() for s in j["steps"] for k in ("commit", "harness_from") if k in s}
    for commit in commits:
        assert subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0, commit


def test_a_job_replays_each_step_from_its_frozen_tree(tmp_path) -> None:
    steps = runner.commands(JOBS["mem-s0"], out_dir=tmp_path, work=tmp_path / "w", dry_run=True)
    assert [Path(cmd[-1]).name for _, cmd, _ in steps] == [f"memorybench-{a}-s0.json" for a in ("A", "M", "P", "M0")]
    wt, cmd, env = steps[-1]
    assert wt.name == "b5750de-h1a1ed02" and env == {"PYTHONPATH": str(wt), "APEX_COMMIT": "b5750de"}
    assert cmd[1] == str(wt / "scripts" / "apex_real_validation.py") and "--arm-label" in cmd
    assert all(str(tmp_path / "w" / "mem-train") in c for _, c, _ in steps)  # une base d'expérience partagée
