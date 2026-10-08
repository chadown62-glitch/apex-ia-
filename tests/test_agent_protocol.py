"""Protocole de l'agent outillé corrigé d'après les traces RÉELLES d'un modèle 1.5B (docs/VALIDATION_REELLE.md §6) :
écritures entre triples guillemets, soumissions prématurées en boucle, répétitions, vérification bon marché sur les
exemples publics de l'énoncé, historique compacté.

Vraie boucle, vrais outils, vraie sandbox, vrai Judge ; modèle DOUBLURE jouant un script. Prouve le mécanisme,
jamais un gain sur un vrai modèle (qui se MESURE).
"""

from __future__ import annotations

import functools

from apex.agents.runtime import AgentRuntime
from apex.policy.model import Subject
from apex.taskloop.agent import compose_prompt, parse_action
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec
from tests.test_taskloop_adaptation import _kinds
from tests.test_taskloop_execution import a_candidate, a_pool
from tests.test_taskloop_tools import ALL, CASES, RIGHT, WRONG, _Actor, act, factory  # noqa: F401

STATEMENT = "Additionne a et b. Exemples : `add(1, 2) == 3`, `add(0, 0) == 0`."


def _run(factory, tmp_path, script, *, statement=STATEMENT, permissions=ALL, steps=10):  # noqa: F811
    _Actor.script = list(script)
    spec = build_user_function_task_spec(function="add", description=statement, cases=CASES)
    task = TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
                             provider_factory=lambda _c: _Actor(mode="ok"), workspace_root=str(tmp_path))
    run = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop).start(
        task_id=task.id, objective="o", wait=True, tool_permissions=permissions, max_tool_steps=steps,
        limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1))
    return task, run


def _steps(factory, run):  # noqa: F811
    return [e for e in _kinds(factory, run.id) if e["kind"] == "agent.step"]


def test_a_write_in_python_triple_quotes_is_accepted_as_written() -> None:
    raw = '```json\n{"tool": "fs.write", "arguments": {"path": "solution.py", "content": """\ndef add(a, b):\n    return a + b\n"""}}\n```'
    action, refusal = parse_action(raw)
    assert refusal is None and action["arguments"]["content"] == "def add(a, b):\n    return a + b"
    assert parse_action("{ pas du json")[0] is None  # une réponse vraiment illisible reste refusée


def test_a_premature_submit_is_told_what_to_do_and_repeats_end_the_episode(factory, tmp_path) -> None:  # noqa: F811
    _, run = _run(factory, tmp_path, [act()] * 6)
    steps = _steps(factory, run)
    assert len(steps) == 4 and all(s["error"] == "nothing_to_submit" for s in steps)  # arrêt à la 4e répétition
    assert '"tool": "fs.write"' in _Actor.prompts[1]  # l'observation dit QUOI faire, exemple d'appel compris
    assert "refusée 2 fois de suite" in _Actor.prompts[2]
    assert run.status.value == "failed"


def test_solution_check_runs_the_public_examples_of_the_statement_only(factory, tmp_path) -> None:  # noqa: F811
    task, run = _run(factory, tmp_path, [
        act("fs.write", path="solution.py", content=WRONG), act("solution.check"),
        act("fs.write", path="solution.py", content=RIGHT), act("solution.check"), act()])
    assert run.status.value == "completed"
    assert "solution.check" in _Actor.prompts[1]  # conseillé juste après l'écriture de solution.py
    check = _Actor.prompts[2].split("observation :")[-1]
    assert '{"call": "add(1, 2)", "expected": "3", "got": "-1", "error": null, "ok": false}' in check
    assert '"examples_failed": 1' in check and "corrige solution.py" in check
    assert "30" not in check and "add(10, 20)" not in check  # le cas CACHÉ (10, 20) -> 30 n'apparaît jamais
    assert "tous les exemples publics passent" in _Actor.prompts[4]


def test_without_examples_the_check_still_reports_import_errors(factory, tmp_path) -> None:  # noqa: F811
    _, run = _run(factory, tmp_path, [
        act("fs.write", path="solution.py", content="def add(a, b)\n    return a + b\n"), act("solution.check"),
        act("fs.write", path="solution.py", content=RIGHT), act()], statement="Additionne a et b.")
    assert "ne s'exécute pas" in _Actor.prompts[2] and "SyntaxError" in _Actor.prompts[2]
    assert run.status.value == "completed"


def test_a_short_backticked_name_does_not_shift_the_example_pairing() -> None:
    """Mesuré (08/10/2026) : avec une borne de longueur DANS le motif, « `n` » n'était pas apparié et l'exemple
    suivant était absorbé — `roman(4) == 'IV'` perdu."""
    from apex.tasks.public_examples import extract_examples

    text = ("Écris `roman` qui prend un entier `n`. Exemples : `roman(4) == 'IV'`, `roman(9) == 'IX'`, `x`, "
            "`roman(0) == None`.")
    assert [e.source for e in extract_examples(text)] == ["roman(4) == 'IV'", "roman(9) == 'IX'", "roman(0) == None"]


def test_the_answer_format_of_the_statement_is_translated_in_tool_mode(factory, tmp_path) -> None:  # noqa: F811
    _, run = _run(factory, tmp_path, [act("fs.write", path="solution.py", content=WRONG), act("solution.check"), act()],
                  statement=STATEMENT + " Réponds UNIQUEMENT avec le code Python de la fonction.")
    assert "Réponds UNIQUEMENT avec le code Python" in _Actor.prompts[0]  # l'énoncé n'est jamais amputé
    assert "signifie : écris ce code dans solution.py avec fs.write" in _Actor.prompts[0]
    assert "signifie" not in compose_prompt("t", [], [], project=True)  # projet : aucune consigne de format
    (check,) = [s for s in _steps(factory, run) if s["tool"] == "solution.check"]
    assert check["check"] == {"imported": True, "examples": 2, "examples_failed": 1}  # issue tracée (comptes)


def test_old_steps_are_summarised_in_one_line() -> None:
    from apex.taskloop.agent import Step

    steps = [Step(i, {"tool": "fs.read", "arguments": {"path": f"f{i}.py"}},
                  {"ok": True, "output": {"content": "x" * 500}}) for i in range(1, 6)]
    prompt = compose_prompt("tâche", [{"name": "fs.read", "description": "", "input_schema": {}}], steps)
    assert "[tour 1] fs.read f1.py -> ok" in prompt and "[tour 2] fs.read f2.py -> ok" in prompt
    assert prompt.count("x" * 500) == 3  # seuls les 3 derniers tours gardent leur observation complète
