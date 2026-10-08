"""Banc d'outils (docs/VALIDATION_REELLE.md §6.2) : les TÂCHES sont cohérentes et ne fuient rien, les MÉTRIQUES
d'outils sont des comptes objectifs sur les tours persistés. Aucun modèle ici : la mesure se fait avec un vrai
modèle (`scripts/apex_real_validation.py toolbench`)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from apex.tasks.public_examples import extract_examples

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import apex_bench_tasks as bench  # noqa: E402

_spec = importlib.util.spec_from_file_location("apex_real_validation", _SCRIPTS / "apex_real_validation.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


def test_the_bench_covers_the_five_required_task_types() -> None:
    categories = [t.category for t in bench.TOOL_BENCH]
    assert {"lecture", "modification", "execution", "test", "multi"} <= set(categories)
    assert len({t.name for t in bench.TOOL_BENCH}) == len(bench.TOOL_BENCH) == 10


@pytest.mark.parametrize("task", bench.TOOL_BENCH, ids=lambda t: t.name)
def test_public_examples_agree_with_the_reference_and_never_overlap_hidden_cases(task) -> None:
    examples = extract_examples(task.statement, task.function)
    assert examples, "chaque énoncé montre au moins un exemple public exécutable"
    assert len(examples) == task.statement.count(f"`{task.function}(")  # aucun exemple perdu à l'extraction
    for example in examples:
        assert task.reference(*example.args) == example.expected, example.source
    public = {json.dumps(list(e.args)) for e in examples}
    assert not public & {json.dumps(list(args)) for args in task.hidden}  # cas cachés ≠ exemples publics
    assert len(task.hidden) >= 8 and task.statement.endswith(bench.ANSWER)


@pytest.mark.parametrize("task", [t for t in bench.TOOL_BENCH if any(p.startswith("tests/") for p in t.seed)],
                         ids=lambda t: t.name)
def test_seeded_public_tests_pass_with_the_reference(task, tmp_path) -> None:
    for path, content in task.seed.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content, encoding="utf-8")
    (tmp_path / "solution.py").write_text(
        f"import sys\nsys.path.insert(0, {str(_SCRIPTS)!r})\n"
        f"from apex_bench_tasks import {task.reference.__name__} as {task.function}\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_information_only_in_files_is_really_absent_from_the_statement() -> None:
    shipping = next(t for t in bench.TOOL_BENCH if t.name == "shipping_cost")
    assert "4.9" not in shipping.statement and "2.35" not in shipping.statement  # tarifs : dans le fichier seul
    assert shipping.needs == ("data/tarifs.json",) and "4.9" in shipping.seed["data/tarifs.json"]
    meters = next(t for t in bench.TOOL_BENCH if t.name == "to_meters")
    assert "1609" not in meters.statement and "1609.344" in meters.seed["helpers/units.py"]
    for task in (shipping, meters):
        assert bench.SELF_CONTAINED in task.statement  # jugé seul : le dire, sans rien révéler


def test_the_modification_task_starts_from_a_buggy_solution_that_fails_its_examples() -> None:
    task = next(t for t in bench.TOOL_BENCH if t.category == "modification")
    namespace: dict = {}
    exec(bench.TOOL_BENCH_SEED_SOLUTION[task.name], namespace)  # noqa: S102 — code de départ du banc, écrit ici
    assert any(namespace[task.function](*e.args) != e.expected for e in extract_examples(task.statement))
    assert bench.bench_spec(task, with_seed=True).workspace_seed["solution.py"].startswith("def median")
    assert bench.bench_spec(task, with_seed=False).workspace_seed is None  # bras sans outils : aucun fichier


def test_hidden_cases_are_computed_by_the_reference_and_stored_as_the_expected_answer() -> None:
    task = bench.TOOL_BENCH[0]
    spec = bench.bench_spec(task, with_seed=False)
    assert all(json.dumps(c["expected"]) in spec.expected_answer or c["expected"] == "" for c in task.cases())
    assert "Ça marche" not in spec.description  # l'entrée d'un cas caché n'est jamais dans l'énoncé


def _step(n, tool=None, args=None, ok=True, error=None, submit=False, check=None):
    step = {"step": n, "tool": tool, "submit": submit, "ok": ok, "error": error}
    if args is not None:
        step["args"] = json.dumps(args)
    if check is not None:
        step["check"] = check
    return step


def test_tool_metrics_separate_useful_calls_from_waste() -> None:
    steps = [
        _step(1, ok=False, error="invalid_action"),
        _step(2, submit=True, ok=False, error="nothing_to_submit"),
        _step(3, submit=True, ok=False, error="nothing_to_submit"),                 # répétition
        _step(4, "fs.read", {"path": "data/tarifs.json"}),                            # utile (nécessaire)
        _step(5, "fs.read", {"path": "data/tarifs.json"}),                            # doublon : inutile
        _step(6, "fs.write", {"path": "solution.py", "content": "v1"}),               # utile
        _step(7, "solution.check", {}, check={"imported": True, "examples": 2, "examples_failed": 1}),  # utile
        _step(8, "solution.check", {}, check={"imported": True, "examples": 2, "examples_failed": 1}),  # doublon
        _step(9, "fs.write", {"path": "solution.py", "content": "v2"}),               # utile, agit sur l'échec
        _step(10, "fs.read", {"path": "nope.txt"}, ok=False, error="not_found"),
        _step(11, submit=True),
    ]
    m = harness.tool_metrics([{"tool_steps": steps}], needs=("data/tarifs.json",))
    assert (m["useful"], m["useless"], m["repetitions"]) == (4, 6, 3)
    assert m["invalid"] == 1 and m["refused_submits"] == 2 and m["errors"]["not_found"] == 1
    assert m["verified"] and m["failed_checks"] == 2 and m["acted_on_failed_check"] == 1
    assert m["needed_file_read"] is True and m["submitted"] is True
    assert harness.tool_metrics([{"tool_steps": []}])["needed_file_read"] is None  # sans fichier nécessaire


def test_toolbench_analysis_pairs_the_new_agent_with_the_old_one_and_with_no_tools() -> None:
    records = []
    outcomes = {"NT": [0, 0, 1, 0], "T0": [0, 1, 0, 0], "T": [1, 1, 1, 0]}
    for arm, successes in outcomes.items():
        for i, ok in enumerate(successes):
            steps = [] if arm == "NT" else [_step(1, "fs.write", {"path": "solution.py", "content": str(i)}),
                                            _step(2, submit=True)]
            records.append({"arm": arm, "seed": 0, "kind": f"k{i}", "category": "lecture" if i == 0 else "execution",
                            "success": bool(ok), "final_score": float(ok), "duration_s": 2.0, "tools_used": {},
                            "attempts": [{"verdict": "pass" if ok else "fail", "category": "incorrect_solution",
                                          "tool_steps": steps, "episode_stop": "submitted"}],
                            "tool_metrics": harness.tool_metrics([{"tool_steps": steps}])})
    result = harness.analyse_toolbench(records)
    assert result["primary"]["H-T1 agent corrigé (T vs T0)"]["second_only"] == 2
    assert result["primary"]["H-T2 outils (T vs NT)"]["second_only"] == 2
    assert result["secondary"]["T vs NT hors lecture"]["n"] == 3  # la tâche « lecture » est exclue
    assert result["arms"]["T"]["by_category"] == {"execution": "2/3", "lecture": "1/1"}
    assert "useful" not in result["arms"]["NT"] and result["arms"]["T"]["useful"] == 4
    assert set(result["holm_significant"]) == set(result["primary"])


# -- banc mémoire (§6.3) ------------------------------------------------------------------------------------------

CONVENTIONS = {  # ce qui n'existe QUE dans la source : énoncé source oui, énoncé cible jamais
    "zephyr": ("4.90", "1.20", "2.35", "7.50", "4.10"),
    "kappa": ("23456789ABCDEFGHJKLMNPQRSTUVWXYZ",),
    "lumen": ("10 %", "25 %", "retire 5"),
    "orion": ("0.8 m", "5.0292", "4828.032", "1.949"),
    "delta": ("à partir de 17", "13.5"),
    "nova": ("JAN FEV MAR", "AOU"),
}


@pytest.mark.parametrize("pair", bench.MEMORY_BENCH, ids=lambda p: p.name)
def test_the_needed_convention_exists_only_in_the_source_experience(pair) -> None:
    for token in CONVENTIONS[pair.name]:
        assert token in pair.source.statement and token not in pair.target.statement, token
    assert pair.source.name in pair.target.statement  # la cible y renvoie par son nom, comme un utilisateur
    for task in (pair.source, pair.target):
        examples = extract_examples(task.statement, task.function)
        assert examples and all(task.reference(*e.args) == e.expected for e in examples)
        public = {json.dumps(list(e.args)) for e in examples}
        assert not public & {json.dumps(list(a)) for a in task.hidden}
    # Aucune entrée cachée de la cible n'apparaît dans l'expérience source (énoncé) : pas de réponse à recopier.
    for args in pair.target.hidden:
        assert json.dumps(list(args), ensure_ascii=False) not in pair.source.statement


def test_the_placebo_copy_forgets_exactly_one_experience(tmp_path) -> None:
    from apex.memory.model import MemoryCategory
    from apex.memory.service import MemoryService
    from apex.policy.model import Subject

    db = tmp_path / "train.db"
    memory = MemoryService(harness.session_factory(db))
    for source in ("task_run:a", "task_run:b"):
        memory.write_system(category=MemoryCategory.EXECUTION, content=f"souvenir {source}", source=source,
                            created_by=Subject.CORE)
    copy = tmp_path / "P.db"
    harness._copy_db(db, copy)
    assert harness._forget(copy, "task_run:a") == 1
    left = MemoryService(harness.session_factory(copy))._repo.list_system_records(categories=["execution"], limit=10)
    assert [r.source for r in left] == ["task_run:b"]
    kept = MemoryService(harness.session_factory(db))._repo.list_system_records(categories=["execution"], limit=10)
    assert len(kept) == 2  # la base d'expérience elle-même n'est jamais modifiée


def test_memorybench_analysis_separates_retrieved_used_and_impact() -> None:
    def rec(arm, kind, success, used=None, shown=None, code=""):
        r = {"arm": arm, "seed": 0, "kind": kind, "success": success, "final_score": float(success),
             "duration_s": 1.0, "source_run_id": f"src-{kind}", "attempts": [{"code": code}],
             "memory": None if arm == "A" else [{"source": f"task_run:src-{kind}"}, {"source": "task_run:other"}],
             "memory_used": [] if used is None else [{"attempt": 1, "items": [
                 {"source": f"task_run:src-{kind}", "used": used, "evidence": ["nombre 2.35"] if used else []}]}]}
        if shown is not None:
            r["memory_shown_old"] = shown
        return r

    records = [rec("A", "k1", False), rec("A", "k2", False), rec("A", "k3", True),
               rec("M", "k1", True, used=True), rec("M", "k2", True, used=True), rec("M", "k3", True, used=False),
               rec("P", "k1", False, used=False), rec("P", "k2", False, used=False), rec("P", "k3", True, used=False),
               rec("M0", "k1", False, shown=[{"source": "task_run:src-k1", "text": "Énoncé : tarifs…"}], code="x = 5"),
               rec("M0", "k2", True, shown=[{"source": "task_run:src-k2", "text": "TARIF = 2.35"}], code="t = 2.35"),
               rec("M0", "k3", True, shown=[], code="")]
    result = harness.analyse_memorybench(records, {"k1": "", "k2": "", "k3": ""})
    assert result["primary"]["H-M1 mémoire (M vs A)"]["second_only"] == 2
    assert result["primary"]["H-M2 contenu pertinent, pas seulement « de la mémoire » (M vs P)"]["second_only"] == 2
    m = result["arms"]["M"]
    assert m["relevant_retrieved"] == 3 and m["used_relevant"] == 2 and m["success_if_used"] == "2/2"
    assert result["arms"]["M0"]["used_relevant"] == 1  # recalculé sur l'ancien rendu, même règle
    assert "used_relevant" not in result["arms"]["A"]
