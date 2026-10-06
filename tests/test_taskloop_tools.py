"""Mode OUTILLÉ du TaskLoop : action -> outil -> observation -> diagnostic -> correction -> vérification -> Judge.

Vraie boucle, vrais outils, vraie sandbox, vrai Judge à cas cachés, vraie base ; seul le modèle est une DOUBLURE
qui joue un script d'actions JSON (et enregistre chaque prompt reçu). Prouve le mécanisme, jamais qu'un vrai
modèle s'en sert bien (à MESURER plus tard).
"""

from __future__ import annotations

import functools
import json
from typing import ClassVar

import pytest

from apex.agents.runtime import AgentRuntime
from apex.policy.model import Subject
from apex.taskloop.agent import compose_prompt, parse_action
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits, TaskRequest
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec
from tests.test_taskloop_adaptation import _kinds
from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool, sqlite_session_factory

ALL = ("workspace.read", "workspace.write", "sandbox.execute")
CASES = [{"args": [1, 2], "expected": 3}, {"args": [-5, 5], "expected": 0}, {"args": [10, 20], "expected": 30}]
WRONG = "def add(a, b):\n    return a - b\n"
RIGHT = "def add(a, b):\n    return a + b\n"
MY_TEST = ("import unittest\nfrom solution import add\n\nclass T(unittest.TestCase):\n"
           "    def test_small(self):\n        self.assertEqual(add(2, 3), 5)\n")


def act(tool=None, **arguments):
    return json.dumps({"action": "submit"} if tool is None else {"tool": tool, "arguments": arguments})


class _Actor(_FakeModelProvider):
    script: ClassVar[list[str]] = []
    prompts: ClassVar[list[str]] = []

    def generate(self, request):
        type(self).prompts.append(request.prompt)
        index = min(len(type(self).prompts) - 1, len(type(self).script) - 1)
        self.text = type(self).script[index]
        return super().generate(request)


@pytest.fixture
def factory():
    _Actor.prompts = []
    return sqlite_session_factory()


def _run(factory, tmp_path, script, *, permissions=ALL, attempts=1, steps=10):
    _Actor.script = list(script)
    spec = build_user_function_task_spec(function="add", description="Additionne a et b.", cases=CASES)
    task = TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
                             provider_factory=lambda _c: _Actor(mode="ok"), workspace_root=str(tmp_path))
    service = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)
    run = service.start(task_id=task.id, objective="o", wait=True, tool_permissions=permissions,
                        max_tool_steps=steps,
                        limits=TaskLimits(max_attempts=attempts, max_retries=attempts - 1, max_candidates=1))
    return task, run, tmp_path / run.id


def test_write_test_observe_failure_fix_verify_submit_then_the_hidden_judge_passes(factory, tmp_path) -> None:
    task, run, workspace = _run(factory, tmp_path, [
        act("fs.write", path="solution.py", content=WRONG),
        act("fs.write", path="tests/test_solution.py", content=MY_TEST),
        act("tests.run", start_dir="tests"),
        act("fs.write", path="solution.py", content=RIGHT),
        act("tests.run", start_dir="tests"),
        act(),
    ])
    assert run.status.value == "completed" and run.attempts[0]["judge_result"] == "pass"
    # L'observation de l'échec de SES tests a bien été rendue au modèle avant la correction.
    after_failure = _Actor.prompts[3]
    assert '"status": "failed"' in after_failure and "test_solution.T.test_small" in after_failure
    assert '"status": "passed"' in _Actor.prompts[5]
    # Jamais les cas cachés ni les valeurs attendues : ni dans un prompt, ni dans l'espace de travail.
    for prompt in _Actor.prompts:
        assert "[-5, 5]" not in prompt and "_APEX_INPUTS" not in prompt and task.expected_answer not in prompt
    files = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file())
    assert files == ["solution.py", "tests/test_solution.py"]
    kinds = [e["kind"] for e in _kinds(factory, run.id)]
    assert kinds.count("agent.step") == 6 and kinds.count("tool.called") == 5 and "workspace.created" in kinds


def test_an_unreadable_action_is_observed_never_guessed(factory, tmp_path) -> None:
    _, run, _ = _run(factory, tmp_path, [
        "je vais écrire la fonction", act("fs.write", path="solution.py", content=RIGHT), act()])
    assert run.status.value == "completed"
    assert "invalid_action" in _Actor.prompts[1] and "aucun objet JSON" in _Actor.prompts[1]


def test_tools_not_granted_are_neither_offered_nor_executable(factory, tmp_path) -> None:
    _, run, workspace = _run(factory, tmp_path, [
        act("fs.write", path="solution.py", content=RIGHT), act("tests.run"), act()],
        permissions=("workspace.read", "workspace.write"))
    first = _Actor.prompts[0]
    assert '"name": "fs.write"' in first and '"name": "tests.run"' not in first and "code.run" not in first
    assert "permission_denied" in _Actor.prompts[2]
    assert run.status.value == "completed"  # le Judge reste l'autorité, avec ou sans outils d'exécution


def test_without_submit_the_step_budget_ends_the_episode_and_solution_py_is_judged(factory, tmp_path) -> None:
    _, run, _ = _run(factory, tmp_path, [act("fs.write", path="solution.py", content=RIGHT),
                                         act("fs.list")], steps=3)
    assert len(_Actor.prompts) == 3 and run.status.value == "completed"


def test_a_failed_attempt_is_corrected_in_the_same_workspace_with_the_judge_facts(factory, tmp_path) -> None:
    task, run, workspace = _run(factory, tmp_path, [
        act("fs.write", path="solution.py", content=WRONG), act(),  # tentative 1 : soumet sans tester
        act("fs.read", path="solution.py"),  # tentative 2 : relit son propre fichier (continuité)
        act("fs.write", path="solution.py", content=RIGHT), act(),
    ], attempts=2)
    assert [a["judge_result"] for a in run.attempts] == [None, "pass"]
    second_attempt_first_prompt = _Actor.prompts[2]
    assert "Observations factuelles" in second_attempt_first_prompt and "3 sur 3" in second_attempt_first_prompt
    assert "return a - b" in _Actor.prompts[3]  # le fichier de la tentative 1 est toujours là


def test_tool_mode_configuration_is_validated_and_persisted(factory, tmp_path) -> None:
    with pytest.raises(ValueError):
        TaskRequest(task_id="t", objective="o", tool_permissions=("root.everything",))
    with pytest.raises(ValueError):
        TaskRequest(task_id="t", objective="o", max_tool_steps=0)
    _, run, _ = _run(factory, tmp_path, [act("fs.write", path="solution.py", content=RIGHT), act()])
    assert run.tool_permissions == ALL and run.max_tool_steps == 10


def test_action_parsing_and_prompt_composition() -> None:
    assert parse_action('```json\n{"tool": "fs.list", "arguments": {}}\n```') == ({"tool": "fs.list",
                                                                                     "arguments": {}}, None)
    assert parse_action('{"action": "submit"}') == ({"action": "submit"}, None)
    assert parse_action('{"tool": "fs.list", "arguments": {}, "shell": "rm -rf /"}')[0] is None
    assert parse_action("[1, 2]")[0] is None
    prompt = compose_prompt("Énoncé", [{"name": "fs.read"}], [])
    assert prompt.index("Outils disponibles") < prompt.index("Énoncé") and "aucun tour" in prompt


def test_submitting_before_writing_solution_py_is_refused_and_observed(factory, tmp_path) -> None:
    """Constat RÉEL (smoke CI, vrai modèle 1.5B) : le modèle soumettait dès le premier tour, sans rien
    écrire. Le refus est une observation sur SON espace de travail, jamais une donnée cachée."""
    _, run, workspace = _run(factory, tmp_path, [act(), act("fs.write", path="solution.py", content=RIGHT), act()])
    assert run.status.value == "completed"
    assert "nothing_to_submit" in _Actor.prompts[1] and "fs.write" in _Actor.prompts[1]
    assert "Exemple de premier tour" in _Actor.prompts[0]
