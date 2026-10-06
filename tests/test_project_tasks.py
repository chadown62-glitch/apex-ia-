"""Tâches « projet » (plusieurs fichiers) : codebase de départ visible, vérifications cachées
`module:fonction(args)`, valeurs attendues sur l'hôte, mode outillé obligatoire.

Vraie boucle, vrais outils, vraie sandbox, vrai Judge ; modèle DOUBLURE jouant un script d'actions.
"""

from __future__ import annotations

import functools
import time

import pytest

from apex.agents.runtime import AgentRuntime
from apex.policy.model import Subject
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskKind, TaskSplit
from apex.tasks.repository import TaskRepository
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import InvalidUserTask, build_user_project_task_spec
from tests.test_taskloop_execution import a_candidate, a_pool, sqlite_session_factory
from tests.test_taskloop_tools import ALL, _Actor, act

SEED = {
    "shop/__init__.py": "",
    "shop/pricing.py": "from shop.tax import TAX\n\ndef total(prices):\n    return sum(prices)\n",
    "shop/tax.py": "TAX = 0.2\n",
    "tests/test_pricing.py": ("import unittest\nfrom shop.pricing import total\n\nclass T(unittest.TestCase):\n"
                              "    def test_empty(self):\n        self.assertEqual(total([]), 0)\n"),
}
CASES = [
    {"module": "shop.pricing", "function": "total", "args": [[10, 20]], "expected": 36.0},
    {"module": "shop.pricing", "function": "total", "args": [[]], "expected": 0.0},
]
FIXED = "from shop.tax import TAX\n\ndef total(prices):\n    return round(sum(prices) * (1 + TAX), 2)\n"


@pytest.fixture
def factory():
    _Actor.prompts = []
    return sqlite_session_factory()


def _task(factory):
    spec = build_user_project_task_spec(description="total() doit inclure la taxe TAX.", seed_files=SEED,
                                        cases=CASES)
    return TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer, kind=TaskKind.PROJECT, workspace_seed=spec.workspace_seed)


def _service(factory, tmp_path):
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
                             provider_factory=lambda _c: _Actor(mode="ok"), workspace_root=str(tmp_path))
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)


def test_a_multi_file_project_is_inspected_fixed_tested_and_judged_by_hidden_calls(factory, tmp_path) -> None:
    task = _task(factory)
    assert TaskRepository(factory).get(task.id).effective_kind is TaskKind.PROJECT
    _Actor.script = [act("project.inspect"), act("fs.read", path="shop/pricing.py"),
                     act("fs.write", path="shop/pricing.py", content=FIXED),
                     act("tests.run", start_dir="tests"), act()]
    run = _service(factory, tmp_path).start(
        task_id=task.id, objective="o", wait=True, tool_permissions=ALL,
        limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1))
    assert run.status.value == "completed" and run.attempts[0]["judge_result"] == "pass"
    assert '"python_packages": ["shop"]' in _Actor.prompts[1]  # l'inspection a vu le projet de départ
    assert "return sum(prices)" in _Actor.prompts[2]
    for prompt in _Actor.prompts:
        assert "36.0" not in prompt and "_APEX_CASES" not in prompt  # valeurs attendues et cas jamais montrés
    assert "`shop.pricing.total`" in _Actor.prompts[0]  # l'interface jugée est connue, pas les cas


def test_an_unfixed_project_fails_with_failure_facts_for_the_next_attempt(factory, tmp_path) -> None:
    task = _task(factory)
    _Actor.script = [act(), act("fs.write", path="shop/pricing.py", content=FIXED), act()]
    run = _service(factory, tmp_path).start(
        task_id=task.id, objective="o", wait=True, tool_permissions=ALL,
        limits=TaskLimits(max_attempts=2, max_retries=1, max_candidates=1))
    assert [a["judge_result"] for a in run.attempts] == [None, "pass"]
    assert "cas en échec : 1 sur 2" in _Actor.prompts[1]  # total([]) == 0 passait déjà, total([10,20]) non


def test_a_project_requires_tool_mode(factory, tmp_path) -> None:
    task = _task(factory)
    with pytest.raises(ValueError):
        _service(factory, tmp_path).start(task_id=task.id, objective="o", wait=True,
                                          limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1))


@pytest.mark.parametrize("bad", [
    {"seed_files": {"../x.py": ""}},
    {"seed_files": {".apex/x.py": ""}},
    {"cases": [{"module": "os; import sys", "function": "f", "args": [], "expected": 1}]},
    {"cases": [{"module": "m", "function": "_private", "args": [], "expected": 1}]},
    {"cases": [{"module": "m", "function": "f", "args": "x", "expected": 1}]},
    {"cases": []},
])
def test_invalid_projects_are_refused(bad) -> None:
    payload = {"description": "d", "seed_files": {"m.py": ""},
               "cases": [{"module": "m", "function": "f", "args": [], "expected": 1}], **bad}
    with pytest.raises(InvalidUserTask):
        build_user_project_task_spec(**payload)


def test_the_project_api_round_trip(factory, monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from apex.api import dependencies
    from apex.api.app import create_app
    from tests.api_test_support import TEST_API_TOKEN

    monkeypatch.setattr(dependencies, "_task_run_service", _service(factory, tmp_path))
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    client = TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})
    created = client.post("/api/v1/tasks/project", json={"description": "taxe", "seed_files": SEED,
                                                           "cases": CASES})
    assert created.status_code == 200, created.text
    task_id = created.json()["data"]["id"]
    assert client.post(f"/api/v1/tasks/{task_id}/runs", json={}).status_code == 422  # sans mode outillé
    _Actor.script = [act("fs.write", path="shop/pricing.py", content=FIXED), act()]
    run = client.post(f"/api/v1/tasks/{task_id}/runs", json={"tool_permissions": list(ALL), "max_attempts": 1})
    assert run.status_code == 202
    for _ in range(400):
        detail = client.get(f"/api/v1/task-runs/{run.json()['data']['id']}").json()["data"]
        if detail["status"] != "running":
            break
        time.sleep(0.05)
    assert detail["status"] == "completed"
    assert client.post("/api/v1/tasks/project", json={"description": "d", "seed_files": {"/etc/x": ""},
                                                       "cases": CASES}).status_code == 422
