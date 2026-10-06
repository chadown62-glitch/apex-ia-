"""REPRISE RÉELLE d'une TaskRun interrompue : état persisté -> reprise -> continuation contrôlée, idempotente.

L'interruption est réelle à l'échelle du processus simulé : le fil d'exécution MEURT (SystemExit, ignoré par
`threading` comme un arrêt) sans rien finir — la ligne reste `running`, puis un NOUVEAU service (nouveau
« processus ») la marque `interrupted` au démarrage, exactement comme `apex serve`. Vraie boucle, vraie sandbox,
vrai Judge à cas cachés ; seul le modèle est une doublure scriptée.
"""

from __future__ import annotations

import functools
import json
from typing import ClassVar

import pytest

from apex.agents.runtime import AgentRuntime
from apex.policy.model import Subject
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService, TaskRunStatus
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec
from tests.test_taskloop_adaptation import _kinds
from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool, sqlite_session_factory

CASES = [{"args": [1, 2], "expected": 3}, {"args": [-5, 5], "expected": 0}, {"args": [10, 20], "expected": 30}]
RIGHT = "def add(a, b):\n    return a + b\n"
WRONG = "def add(a, b):\n    return a - b\n"
CRASH = object()
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")


class _Script(_FakeModelProvider):
    """Joue `script` appel après appel ; `CRASH` tue le fil d'exécution comme un arrêt du processus."""

    script: ClassVar[list] = []
    prompts: ClassVar[list[str]] = []

    def generate(self, request):
        type(self).prompts.append(request.prompt)
        step = type(self).script.pop(0) if type(self).script else RIGHT
        if step is CRASH:
            raise SystemExit("processus APEX arrêté")
        self.text = step
        return super().generate(request)


@pytest.fixture
def factory():
    _Script.script, _Script.prompts = [], []
    return sqlite_session_factory()


def _task(factory):
    spec = build_user_function_task_spec(function="add", description="Additionne a et b.", cases=CASES)
    return TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)


def _service(factory, tmp_path, *, crash_after_loop=False):
    registry = TaskLoopRegistry()
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=registry,
                             provider_factory=lambda _c: _Script(), workspace_root=str(tmp_path))
    if crash_after_loop:
        def loop_then_crash(*args, **kwargs):
            loop(*args, **kwargs)
            raise SystemExit("processus arrêté juste avant d'enregistrer le résultat")
        return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop_then_crash, registry=registry)
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop, registry=registry)


def _interrupted(factory, tmp_path, script, *, attempts=3, crash_after_loop=False, **kwargs):
    """Lance une TaskRun que le « processus » interrompt, puis redémarre : la TaskRun est `interrupted`."""
    _Script.script = list(script)
    task = _task(factory)
    run = _service(factory, tmp_path, crash_after_loop=crash_after_loop).start(
        task_id=task.id, objective="o", wait=True,
        limits=TaskLimits(max_attempts=attempts, max_retries=attempts - 1, max_candidates=1), **kwargs)
    assert run.status is TaskRunStatus.RUNNING  # le fil est mort sans rien terminer
    fresh = _service(factory, tmp_path)  # nouveau processus
    assert fresh.reconcile_on_startup() == 1
    assert fresh.repo.get(run.id).status is TaskRunStatus.INTERRUPTED
    _Script.prompts = []
    return task, run, fresh


def test_resume_continues_after_the_judged_attempts_with_their_failure_facts(factory, tmp_path) -> None:
    task, run, fresh = _interrupted(factory, tmp_path, [WRONG, CRASH])
    _Script.script = [RIGHT]
    resumed = fresh.resume(run.id, wait=True)
    assert resumed.id == run.id and resumed.status is TaskRunStatus.COMPLETED and resumed.resume_count == 1
    # Une seule génération après reprise : la tentative 1 (jugée avant l'arrêt) n'est pas refaite.
    (prompt,) = _Script.prompts
    assert "Observations factuelles" in prompt and "3 sur 3" in prompt  # faits d'échec relus de la base
    assert [a["attempt_number"] for a in resumed.attempts] == [1, 2]
    assert [a["judge_result"] for a in resumed.attempts] == [None, "pass"] and len(resumed.eval_run_ids) == 2
    (event,) = [e for e in _kinds(factory, run.id) if e["kind"] == "task.resumed"]
    assert event["judged_attempts"] == 1 and event["eval_run_ids"] == [resumed.eval_run_ids[0]]
    assert "[-5, 5]" not in prompt and task.expected_answer not in prompt


def test_a_run_already_judged_pass_before_the_crash_is_finished_without_any_new_generation(factory, tmp_path) -> None:
    _, run, fresh = _interrupted(factory, tmp_path, [RIGHT], crash_after_loop=True)
    resumed = fresh.resume(run.id, wait=True)
    assert resumed.status is TaskRunStatus.COMPLETED and _Script.prompts == []
    assert resumed.final_output and len(resumed.eval_run_ids) == 1  # sortie relue de l'EvalRun jugée PASS
    assert [a["judge_result"] for a in resumed.attempts] == ["pass"]


def test_the_attempt_budget_spans_the_interruption(factory, tmp_path) -> None:
    _, run, fresh = _interrupted(factory, tmp_path, [WRONG, WRONG], attempts=2, crash_after_loop=True)
    resumed = fresh.resume(run.id, wait=True)
    assert resumed.status is TaskRunStatus.FAILED and resumed.stop_reason == "max_attempts_reached"
    assert _Script.prompts == []  # budget déjà consommé avant l'arrêt : aucune tentative de plus


def test_resume_is_idempotent_and_refused_outside_interrupted(factory, tmp_path) -> None:
    _, run, fresh = _interrupted(factory, tmp_path, [WRONG, CRASH])
    assert fresh.repo.claim_resume(run.id) is True
    assert fresh.repo.claim_resume(run.id) is False  # seconde reprise concurrente : refusée par la base
    with pytest.raises(ValueError, match="interrompue"):
        fresh.resume(run.id)
    with pytest.raises(LookupError):
        fresh.resume("inconnue")


def test_the_plan_written_before_the_interruption_is_reused_not_regenerated(factory, tmp_path) -> None:
    plan = "1. additionner a et b\n2. renvoyer la somme"
    _, run, fresh = _interrupted(factory, tmp_path, [plan, WRONG, CRASH], plan_first=True)
    _Script.script = [RIGHT]
    resumed = fresh.resume(run.id, wait=True)
    assert resumed.status is TaskRunStatus.COMPLETED
    (prompt,) = _Script.prompts  # aucun nouvel appel PLAN
    assert plan in prompt
    assert [e["kind"] for e in _kinds(factory, run.id)].count("task.plan.reused") == 1


def test_the_tool_workspace_survives_the_interruption(factory, tmp_path) -> None:
    write_wrong = json.dumps({"tool": "fs.write", "arguments": {"path": "solution.py", "content": WRONG}})
    submit = json.dumps({"action": "submit"})
    read = json.dumps({"tool": "fs.read", "arguments": {"path": "solution.py"}})
    write_right = json.dumps({"tool": "fs.write", "arguments": {"path": "solution.py", "content": RIGHT}})
    _, run, fresh = _interrupted(
        factory, tmp_path, [write_wrong, submit, CRASH],
        tool_permissions=("workspace.read", "workspace.write", "sandbox.execute"), max_tool_steps=5)
    _Script.script = [read, write_right, submit]
    resumed = fresh.resume(run.id, wait=True)
    assert resumed.status is TaskRunStatus.COMPLETED
    assert "return a - b" in _Script.prompts[1]  # le fichier écrit avant l'arrêt est toujours là
    assert (tmp_path / run.id / "solution.py").read_text() == RIGHT


def test_a_mission_run_is_not_resumed_behind_the_missions_back(factory, tmp_path) -> None:
    from apex.missions.service import MissionService

    task, run, fresh = _interrupted(factory, tmp_path, [WRONG, CRASH])
    mission = MissionService(factory).create(title="m", description="d", specialty="research", objective="o",
                                             created_by=Subject.HUMAN, task_id=task.id)
    from apex.db.engine import session_scope
    from apex.db.models import TaskRunRow

    with session_scope(factory) as session:
        session.get(TaskRunRow, run.id).mission_id = mission.id
    with pytest.raises(ValueError, match="recréer la mission"):
        fresh.resume(run.id)
