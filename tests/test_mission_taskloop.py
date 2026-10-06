"""Unification Missions/TaskLoop : une mission à Task vérifiable est exécutée par LE chemin autonome (TaskLoop).

Vraie base, vrai MissionService, vrai Scheduler, vrai TaskRunService, vraie boucle, vraie sandbox, vrai Judge à
cas cachés ; seul le modèle est une doublure. Prouve : la mission ne fait que gouverner (file, RUNNING, REVIEW
humaine / FAILED), toute l'exécution (tentatives, Judge, Experience) est celle du TaskLoop, reliée par
`task_run_id`/`mission_id` ; annulation réelle ; aucune permission d'outil implicite.
"""

from __future__ import annotations

import functools
import json
import threading
import time

import pytest

from apex.agents.runtime import AgentRuntime
from apex.missions.execution import run_mission_task
from apex.missions.service import MissionService
from apex.missions.states import MissionState
from apex.policy.model import Subject
from apex.scheduler.service import SchedulerService
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskKind, TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec, build_user_project_task_spec
from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool, sqlite_session_factory

CASES = [{"args": [1, 2], "expected": 3}, {"args": [-5, 5], "expected": 0}, {"args": [10, 20], "expected": 30}]
RIGHT = "def add(a, b):\n    return a + b\n"
WRONG = "def add(a, b):\n    return a - b\n"


@pytest.fixture
def factory():
    return sqlite_session_factory()


def _task(factory):
    spec = build_user_function_task_spec(function="add", description="Additionne a et b.", cases=CASES)
    return TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)


def _runs(factory, provider, registry=None):
    registry = registry or TaskLoopRegistry()
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=registry,
                             provider_factory=lambda _c: provider)
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop, registry=registry)


def _mission(factory, task, **kwargs):
    return MissionService(factory).create(
        title="Additionner", description="Contexte de la mission.", specialty="research",
        objective="Écrire add", created_by=Subject.HUMAN, task_id=task.id, **kwargs)


def test_a_mission_is_executed_by_the_taskloop_then_awaits_human_review(factory) -> None:
    task = _task(factory)
    mission = _mission(factory, task, task_options={"max_attempts": 2})
    service = MissionService(factory)
    done = run_mission_task(mission.id, mission_service=service,
                            task_run_service=_runs(factory, _FakeModelProvider(text=RIGHT)))
    assert done.state is MissionState.REVIEW and done.task_run_id is not None
    summary = json.loads(done.result)
    assert summary["task_run_id"] == done.task_run_id and summary["verdicts"] == ["pass"]
    run = TaskRunService(factory, AgentRuntime(factory), a_pool()).repo.get(done.task_run_id)
    assert run.mission_id == mission.id and run.status.value == "completed"
    # La validation finale reste humaine : REVIEW -> COMPLETED seulement sur décision explicite.
    assert service.validate(mission.id, requested_by=Subject.HUMAN).state is MissionState.COMPLETED


def test_a_mission_whose_judge_never_passes_fails_with_the_run_reference(factory) -> None:
    task = _task(factory)
    mission = _mission(factory, task, task_options={"max_attempts": 2})
    done = run_mission_task(mission.id, mission_service=MissionService(factory),
                            task_run_service=_runs(factory, _FakeModelProvider(text=WRONG)))
    assert done.state is MissionState.FAILED and done.task_run_id in done.error
    run = TaskRunService(factory, AgentRuntime(factory), a_pool()).repo.get(done.task_run_id)
    assert [a["judge_result"] for a in run.attempts] == [None, None]  # deux tentatives du TaskLoop, jugées


def test_the_scheduler_runs_task_missions_without_any_assigned_agent(factory) -> None:
    task = _task(factory)
    mission = _mission(factory, task)
    service = MissionService(factory)
    scheduler = SchedulerService(factory, mission_service=service, agent_runtime=AgentRuntime(factory),
                                 max_concurrent_missions=1,
                                 task_run_service=_runs(factory, _FakeModelProvider(text=RIGHT)))
    try:
        assert scheduler.tick().submitted == (mission.id,)
        assert scheduler.wait_for_idle(timeout_s=60)
    finally:
        scheduler.shutdown(wait=True)
    assert service.get(mission.id).state is MissionState.REVIEW


def test_without_a_task_runner_the_scheduler_leaves_the_mission_queued(factory) -> None:
    mission = _mission(factory, _task(factory))
    scheduler = SchedulerService(factory, mission_service=MissionService(factory),
                                 agent_runtime=AgentRuntime(factory), max_concurrent_missions=1)
    try:
        result = scheduler.tick()
    finally:
        scheduler.shutdown(wait=True)
    assert result.skipped_no_task_runner == (mission.id,) and result.submitted == ()
    assert MissionService(factory).get(mission.id).state is MissionState.QUEUED


def test_cancelling_a_running_task_mission_really_stops_its_taskrun() -> None:
    from tests.db_test_support import threaded_sqlite_session_factory

    factory = threaded_sqlite_session_factory()  # le test sonde la base PENDANT que la TaskRun tourne
    provider = _FakeModelProvider(mode="blocked", text=RIGHT)
    provider.release = threading.Event()
    registry = TaskLoopRegistry()
    service = MissionService(factory)
    runs = _runs(factory, provider, registry)
    mission = _mission(factory, _task(factory))
    scheduler = SchedulerService(factory, mission_service=service, agent_runtime=AgentRuntime(factory),
                                 max_concurrent_missions=1, task_run_service=runs, cancel_wait_timeout_s=10)
    try:
        scheduler.tick()
        for _ in range(200):  # la TaskRun a démarré et le modèle « réfléchit »
            if service.get(mission.id).task_run_id and provider.calls:
                break
            time.sleep(0.05)
        run_id = service.get(mission.id).task_run_id
        threading.Timer(0.3, provider.release.set).start()  # la génération finit APRÈS la demande d'annulation
        cancelled = scheduler.cancel_mission(mission.id, requested_by=Subject.HUMAN)
        assert scheduler.wait_for_idle(timeout_s=30)
    finally:
        provider.release.set()
        scheduler.shutdown(wait=True)
    assert cancelled.state is MissionState.CANCELLED
    run = runs.repo.get(run_id)
    assert run.status.value == "cancelled" and run.eval_run_ids == ()  # rien exécuté ni jugé après l'annulation
    assert service.get(mission.id).state is MissionState.CANCELLED


def test_a_refused_start_fails_the_mission_instead_of_leaving_it_running(factory) -> None:
    task = _task(factory)
    mission = _mission(factory, task)

    class _Refusing:
        def start(self, **_kwargs):
            raise RuntimeError("une exécution de la tâche est déjà en cours")

    done = run_mission_task(mission.id, mission_service=MissionService(factory), task_run_service=_Refusing())
    assert done.state is MissionState.FAILED and "déjà en cours" in done.error


def test_task_missions_are_validated_at_creation_and_never_get_implicit_permissions(factory) -> None:
    task = _task(factory)
    service = MissionService(factory)
    with pytest.raises(ValueError, match="introuvable"):
        service.create(title="t", description="d", specialty="research", objective="o",
                       created_by=Subject.HUMAN, task_id="inconnue")
    with pytest.raises(ValueError, match="inconnue"):
        _mission(factory, task, task_options={"shell": True})
    with pytest.raises(ValueError, match="permissions connues"):
        _mission(factory, task, task_options={"tool_permissions": ["root.everything"]})
    with pytest.raises(ValueError, match="task_options exige task_id"):
        service.create(title="t", description="d", specialty="research", objective="o",
                       created_by=Subject.HUMAN, task_options={"max_attempts": 1})
    spec = build_user_project_task_spec(
        description="Projet.", seed_files={"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 0\n"},
        cases=[{"module": "pkg.m", "function": "f", "args": [], "expected": 1}])
    project = TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer, kind=TaskKind.PROJECT, workspace_seed=spec.workspace_seed)
    with pytest.raises(ValueError, match="workspace.write"):
        _mission(factory, project)
    granted = _mission(factory, project, task_options={"tool_permissions": ["workspace.write", "workspace.read"]})
    assert granted.task_options["tool_permissions"] == ["workspace.read", "workspace.write"]
    assert _mission(factory, task).task_options["tool_permissions"] == []  # rien n'est accordé par défaut


def test_the_task_run_cancel_route_reports_what_it_really_did(factory, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apex.api import dependencies
    from apex.api.app import create_app
    from tests.api_test_support import TEST_API_TOKEN

    runs = _runs(factory, _FakeModelProvider(text=RIGHT))
    run = runs.start(task_id=_task(factory).id, objective="o", wait=True)
    monkeypatch.setattr(dependencies, "_task_run_service", runs)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    client = TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})
    body = client.post(f"/api/v1/task-runs/{run.id}/cancel").json()["data"]
    assert body == {"run_id": run.id, "status": "completed", "cancel_requested": False}  # déjà terminée
    assert client.post("/api/v1/task-runs/inconnue/cancel").status_code == 404
