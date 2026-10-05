"""Tâches définies par l'utilisateur (`POST /tasks/function`) et trace d'exécution (`GET /task-runs/{id}`).

Vraie API, vraie boucle, vraie sandbox, vrai Judge ; seul le modèle est une doublure (le code qu'il « génère »
est fixé par le test). Prouve : construction sûre du harnais, valeurs attendues jamais dans la sandbox, refus
des entrées dangereuses ou mal formées, exécution de bout en bout, trace réelle exposée.
"""

from __future__ import annotations

import ast
import functools
import json
import time

import pytest

from apex.taskgen.solution_tasks import InvalidUserTask, build_user_function_task_spec

ADD_CASES = [{"args": [1, 2], "expected": 3}, {"args": [-4, 4], "expected": 0}, {"args": [10, 5], "expected": 15}]


def _harness_inputs(harness: str):
    tree = ast.parse(harness)
    (assign,) = [n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "_APEX_INPUTS"]
    return ast.literal_eval(assign.value)


# -- construction et refus -----------------------------------------------------------------------------


def test_the_harness_carries_only_the_inputs_as_literals_and_never_the_expected_values() -> None:
    hostile = "'); import os; os.system('rm -rf /') #"
    spec = build_user_function_task_spec(
        function="add", description="Additionne a et b.",
        cases=[*ADD_CASES, {"args": [hostile, "x"], "expected": "SECRET-EXPECTED"}],
    )
    assert _harness_inputs(spec.solution_harness) == [[1, 2], [-4, 4], [10, 5], [hostile, "x"]]
    assert "SECRET-EXPECTED" not in spec.solution_harness and "SECRET-EXPECTED" not in spec.description
    assert json.loads(spec.expected_answer) == [3, 0, 15, "SECRET-EXPECTED"]
    calls = [n for n in ast.walk(ast.parse(spec.solution_harness)) if isinstance(n, ast.Call)]
    assert not [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "system"]
    assert "`add`" in spec.description and "2 argument(s)" in spec.description


@pytest.mark.parametrize(
    "function",
    ["__import__", "print", "class", "_apex_inputs", "Add", "a-b", "a b", "x" * 65, "os;import", "", "1abc"],
)
def test_dangerous_or_invalid_function_names_are_refused(function) -> None:
    with pytest.raises(InvalidUserTask):
        build_user_function_task_spec(function=function, description="d", cases=ADD_CASES)


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [{"args": [1]}],
        [{"args": [1], "expected": 1, "code": "x"}],
        [{"args": "1", "expected": 1}],
        [{"args": list(range(11)), "expected": 1}],
        [{"args": [float("nan")], "expected": 1}],
        [{"args": [1], "expected": float("inf")}],
        [{"args": [1], "expected": 1}, {"args": [1], "expected": 2}],
        [{"args": [i], "expected": i} for i in range(51)],
        [{"args": ["x" * 100_001], "expected": 1}],
        [{"args": [{1: 2}], "expected": 1}],
    ],
)
def test_malformed_cases_are_refused(cases) -> None:
    with pytest.raises(InvalidUserTask):
        build_user_function_task_spec(function="f", description="d", cases=cases)


def test_an_empty_or_huge_description_is_refused() -> None:
    for description in ("   ", "x" * 4001):
        with pytest.raises(InvalidUserTask):
            build_user_function_task_spec(function="f", description=description, cases=ADD_CASES)


# -- bout en bout par l'API -----------------------------------------------------------------------------


def _client(factory, monkeypatch, code: str):
    from fastapi.testclient import TestClient

    from apex.agents.runtime import AgentRuntime
    from apex.api import dependencies
    from apex.api.app import create_app
    from apex.taskloop.loop import run_task_loop
    from apex.taskloop.registry import TaskLoopRegistry
    from apex.taskloop.runs import TaskRunService
    from tests.api_test_support import TEST_API_TOKEN
    from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool

    loop = functools.partial(
        run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _FakeModelProvider(mode="ok", text=code),
    )
    monkeypatch.setattr(dependencies, "_task_run_service",
                        TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop))
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    return TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})


def _wait(client, run_id):
    for _ in range(600):
        detail = client.get(f"/api/v1/task-runs/{run_id}").json()["data"]
        if detail["status"] not in ("running", "pending"):
            return detail
        time.sleep(0.05)
    raise AssertionError("TaskRun jamais terminée")


@pytest.fixture
def factory():
    from tests.test_taskloop_execution import sqlite_session_factory

    return sqlite_session_factory()


def test_a_user_task_is_solved_end_to_end_with_a_real_judge_and_a_visible_trace(factory, monkeypatch) -> None:
    client = _client(factory, monkeypatch, "def add(a, b):\n    return a + b\n")
    created = client.post("/api/v1/tasks/function", json={
        "title": "Addition", "description": "Additionne deux entiers.", "function": "add", "cases": ADD_CASES,
    })
    assert created.status_code == 200 and created.json()["status"] == "real"
    task = created.json()["data"]
    started = client.post(f"/api/v1/tasks/{task['id']}/runs", json={"max_attempts": 2})
    assert started.status_code == 202
    detail = _wait(client, started.json()["data"]["id"])
    assert detail["status"] == "completed" and detail["attempts"][0]["judge_result"] == "pass"
    assert detail["retry_adaptation"] == "failure_facts" and detail["plan_first"] is False
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds[0] == "task.created" and "judge.completed" in kinds and kinds[-1].startswith("task.")
    assert {e["trace_id"] for e in detail["events"]} == {detail["id"]}


def test_a_wrong_user_solution_fails_and_the_retry_is_adapted(factory, monkeypatch) -> None:
    client = _client(factory, monkeypatch, "def add(a, b):\n    return a - b\n")
    task = client.post("/api/v1/tasks/function", json={
        "description": "Additionne deux entiers.", "function": "add", "cases": ADD_CASES,
    }).json()["data"]
    run = client.post(f"/api/v1/tasks/{task['id']}/runs", json={"max_attempts": 2}).json()["data"]
    detail = _wait(client, run["id"])
    assert detail["status"] in ("failed", "blocked")
    assert all(a["judge_result"] is None for a in detail["attempts"])
    adapted = [e for e in detail["events"] if e["kind"] == "strategy.adapted"]
    assert adapted and "cas en échec : 3 sur 3" in adapted[0]["genome_content"]["generation"]["prompt_suffix"]


def test_the_api_refuses_invalid_user_tasks_and_run_parameters(factory, monkeypatch) -> None:
    client = _client(factory, monkeypatch, "def add(a, b):\n    return a + b\n")
    bad = client.post("/api/v1/tasks/function", json={"description": "d", "function": "__import__",
                                                       "cases": ADD_CASES})
    assert bad.status_code == 422
    assert client.post("/api/v1/tasks/function", json={"description": "d", "function": "f", "cases": []}
                       ).status_code == 422
    task = client.post("/api/v1/tasks/function", json={"description": "d", "function": "add", "cases": ADD_CASES}
                       ).json()["data"]
    assert client.post(f"/api/v1/tasks/{task['id']}/runs", json={"retry_adaptation": "libre"}).status_code == 422


def test_the_reference_task_kinds_are_listed(factory, monkeypatch) -> None:
    from apex.taskgen.solution_tasks import REFERENCE_TASK_KINDS

    client = _client(factory, monkeypatch, "")
    body = client.get("/api/v1/tasks/reference/kinds").json()
    assert body["status"] == "real" and body["data"] == list(REFERENCE_TASK_KINDS)
