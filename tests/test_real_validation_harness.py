"""Fonctions PURES du harnais de validation réelle (`scripts/apex_real_validation.py`) : lecture du score du
Judge, tests exacts, construction des tâches. Le harnais lui-même s'exécute avec un vrai modèle (CI)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "apex_real_validation.py"
_spec = importlib.util.spec_from_file_location("apex_real_validation", _PATH)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


@pytest.mark.parametrize("detail,score", [
    ("12/12 résultats conformes aux valeurs attendues", 1.0),
    ("3/12 cas incorrects (indices [1, 2, 3])", 0.75),
    ("8/8 cas incorrects (indices [...]), dont 8 exception(s) levée(s) par le candidat", 0.0),
    ("le script n'a pas terminé normalement — code de sortie 1", 0.0),
    (None, 0.0),
])
def test_judge_score_is_read_from_the_judge_verdict(detail, score) -> None:
    assert harness.judge_score(detail) == pytest.approx(score)


def test_exact_mcnemar_and_holm() -> None:
    assert harness.mcnemar_exact(0, 0) == 1.0
    assert harness.mcnemar_exact(6, 0) == pytest.approx(2 / 64)
    assert harness.mcnemar_exact(3, 3) == 1.0
    decisions = harness.holm({"a": 0.01, "b": 0.04, "c": 0.5})
    assert decisions == {"a": True, "b": False, "c": False}


def test_memory_targets_have_hidden_cases_computed_by_a_reference_implementation() -> None:
    import ast
    import json

    pairs = harness.memory_pairs()
    assert len(pairs) == 8
    for source, spec in pairs:
        expected = json.loads(spec.expected_answer)
        assert len(expected) >= 7
        # Les valeurs attendues ne sont ni dans l'énoncé ni dans le harnais exécuté en sandbox.
        tree = ast.parse(spec.solution_harness)
        assert not any(isinstance(n, ast.Name) and n.id == "expected" for n in ast.walk(tree))
    assert harness._int_to_roman(1994) == "MCMXCIV" and harness._int_to_roman(0) is None
    assert harness._rle_decode("q10r1") == "q" * 10 + "r"
    assert harness._max_depth("(()") == -1 and harness._max_depth("(a(b))") == 2


def test_projects_and_hostile_tasks_build() -> None:
    from apex.tasks.solution_tasks import build_user_project_task_spec

    for project in harness.PROJECTS.values():
        spec = build_user_project_task_spec(description=project["description"], seed_files=project["seed"], cases=[
            {"module": m, "function": f, "args": a, "expected": e} for m, f, a, e in project["cases"]])
        assert spec.workspace_seed == project["seed"]
    assert set(harness.ARMS) == {"A", "B", "R", "C", "D"}


def test_paired_analysis_counts_discordant_instances() -> None:
    records = []
    for i, (a, c) in enumerate([(False, True), (False, True), (True, True), (True, False), (False, False)]):
        for arm, success in (("A", a), ("C", c)):
            records.append({"arm": arm, "seed": 0, "kind": f"k{i}", "success": success, "first_pass": success,
                            "corrected": False, "final_score": float(success), "first_score": float(success),
                            "n_attempts": 1, "duration_s": 1.0, "plan_failed": False, "attempts": []})
    result = harness.analyse_adaptation(records)
    assert result["secondary"]["C vs A"]["second_only"] == 2 and result["secondary"]["C vs A"]["first_only"] == 1


def test_tool_results_split_across_jobs_are_analysed_as_one(tmp_path) -> None:
    import json

    def rec(**kw):
        return {"success": False, "final_score": 0.0, "tools_used": {}, "invalid_actions": 0, "agent_steps": 1,
                "tool_errors": [], "attempts": [], **kw}

    files = []
    for i, (section, records, pwned) in enumerate((
            ("function", [rec(seed=0, kind="k", arm="sans_outils"), rec(seed=0, kind="k", arm="outillé", success=True,
                                                                       tools_used={"fs.write": 2})], False),
            ("hostile", [rec(seed=0, hostile="etc_passwd")], True))):
        path = tmp_path / f"t{i}.json"
        path.write_text(json.dumps({"phase": "tools", "sections": [section], "host_sentinel_created": pwned,
                                    "records": {section: records}}), encoding="utf-8")
        files.append(path)
    out = tmp_path / "analysis.json"
    harness.phase_analyse(files, out)
    tools = json.loads(out.read_text(encoding="utf-8"))["tools"]
    assert tools["tools_vs_no_tools"]["outillé_only"] == 1 and tools["function"]["outillé"]["tools_used"] == {"fs.write": 2}
    assert len(tools["hostile"]) == 1 and tools["host_sentinel_created"] is True  # un seul témoin créé suffit
