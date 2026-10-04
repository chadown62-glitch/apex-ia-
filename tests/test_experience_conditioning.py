"""RUN 1 -> Experience -> RUN 2 : une TaskRun conditionnée par les `Experience` d'une TaskRun précédente.

Ce qui est vérifié ici, avec la VRAIE boucle (`run_task_loop`), la vraie base (SQLite, même schéma), la
vraie sandbox et le vrai Judge Tier — **le modèle seul est une doublure** qui ENREGISTRE ce qu'on lui
envoie : on prouve que l'Experience est récupérée (B), utilisée (C) et qu'elle change la stratégie
réellement transmise au modèle (D). Que le comportement d'un VRAI modèle change (E) et que la performance
s'améliore (F) ne peut se mesurer qu'avec un vrai modèle : `scripts/apex_experience_ab.py`
(docs/EXPERIENCE_RUN1_RUN2.md) — jamais ici.
"""

from __future__ import annotations

import functools
import json

import pytest
from sqlalchemy import delete

from apex.agents.runtime import AgentRuntime
from apex.metalearner.adaptive_solution_loop import _DEFAULT_BASE_TEMPERATURE, _INCORRECT_SOLUTION_HINT
from apex.metalearner.experience import ExperienceRepository
from apex.taskgen.solution_tasks import build_palindrome_task_spec
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService
from tests.test_task_runs import _task
from tests.test_taskloop_execution import (
    _WRONG_IS_PALINDROME,
    _FakeModelProvider,
    a_candidate,
    a_pool,
    sqlite_session_factory,
)

ONE_ATTEMPT = TaskLimits(max_attempts=1, max_retries=0)


class _RecordingProvider(_FakeModelProvider):
    """Doublure qui garde chaque `GenerationRequest` reçue — jamais un vrai modèle."""

    requests: list = []

    def generate(self, request):
        type(self).requests.append(request)
        return super().generate(request)


@pytest.fixture
def factory():
    _RecordingProvider.requests = []
    return sqlite_session_factory()


def _service(factory) -> TaskRunService:
    loop = functools.partial(
        run_task_loop,
        candidates=(a_candidate(),),
        registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _RecordingProvider(mode="ok", text=_WRONG_IS_PALINDROME),
    )
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)


def _run(service, task_id, **kwargs):
    return service.start(task_id=task_id, objective="o", limits=ONE_ATTEMPT, wait=True, **kwargs)


def _experiences_of(factory, task_id, run_id):
    return [e for e in ExperienceRepository(factory).list_by_task(task_id) if e.experiment_ref == run_id]


def test_baseline_run_is_unchanged_no_experience_read(factory) -> None:
    task = _task(factory)
    service = _service(factory)
    _run(service, task.id)  # RUN 1, dont l'expérience existe
    baseline = _run(service, task.id)  # RUN 2 SANS condition

    request = _RecordingProvider.requests[-1]
    assert request.prompt == task.description and request.temperature is None
    (experience,) = _experiences_of(factory, task.id, baseline.id)
    assert experience.generation_config == {} and experience.parent_experience_id is None
    assert baseline.condition_on_run is None


def test_conditioned_run_uses_run1_experience_to_change_the_strategy_sent_to_the_model(factory) -> None:
    task = _task(factory)
    service = _service(factory)
    run1 = _run(service, task.id)
    (exp1,) = _experiences_of(factory, task.id, run1.id)
    assert exp1.failure_category.value == "incorrect_solution"  # A : l'expérience existe

    run2 = _run(service, task.id, condition_on_run=run1.id)

    request = _RecordingProvider.requests[-1]
    # D : la stratégie RÉELLEMENT envoyée diffère, et pour la raison tirée de l'expérience.
    assert request.prompt == f"{task.description}\n\n{_INCORRECT_SOLUTION_HINT}"
    assert request.temperature == pytest.approx(_DEFAULT_BASE_TEMPERATURE + 0.4)
    # B/C : provenance traçable dans l'Experience produite et dans la TaskRun.
    (exp2,) = _experiences_of(factory, task.id, run2.id)
    assert exp2.parent_experience_id == exp1.id and exp2.generation_number == 1
    assert exp2.generation_config == {
        "generation": {"temperature": request.temperature, "prompt_suffix": _INCORRECT_SOLUTION_HINT}
    }
    assert "réponse incorrecte" in exp2.mutation_description
    assert run2.condition_on_run == run1.id
    assert service.repo.get(run2.id).condition_on_run == run1.id  # persisté


def test_ablation_without_the_persisted_experience_the_strategy_falls_back_to_baseline(factory) -> None:
    """La décision dépend de l'Experience PERSISTÉE, pas d'un état en mémoire : supprimée, plus d'effet."""
    from apex.db.engine import session_scope
    from apex.db.models import ExperienceRow

    task = _task(factory)
    service = _service(factory)
    run1 = _run(service, task.id)
    with session_scope(factory) as session:
        session.execute(delete(ExperienceRow).where(ExperienceRow.experiment_ref == run1.id))

    run2 = _run(service, task.id, condition_on_run=run1.id)

    request = _RecordingProvider.requests[-1]
    assert request.prompt == task.description and request.temperature is None
    (exp2,) = _experiences_of(factory, task.id, run2.id)
    assert exp2.parent_experience_id is None
    assert "aucune expérience" in exp2.mutation_description  # l'absence est tracée, jamais masquée


def test_a_chain_run3_conditioned_on_run2_reads_generation_1(factory) -> None:
    task = _task(factory)
    service = _service(factory)
    run1 = _run(service, task.id)
    run2 = _run(service, task.id, condition_on_run=run1.id)
    run3 = _run(service, task.id, condition_on_run=run2.id)

    (exp2,) = _experiences_of(factory, task.id, run2.id)
    (exp3,) = _experiences_of(factory, task.id, run3.id)
    assert exp3.parent_experience_id == exp2.id and exp3.generation_number == 2


def test_no_hidden_test_data_ever_reaches_the_prompt(factory) -> None:
    task = _task(factory)
    service = _service(factory)
    run1 = _run(service, task.id)
    _run(service, task.id, condition_on_run=run1.id)

    prompt = _RecordingProvider.requests[-1].prompt
    spec = build_palindrome_task_spec()
    assert spec.expected_answer not in prompt
    hidden_inputs = [
        s
        for s in json.loads(
            spec.solution_harness.split("_APEX_INPUTS = ")[1].splitlines()[0].replace("'", '"')
        )
        if len(s) > 3
    ]
    assert hidden_inputs and not any(s in prompt for s in hidden_inputs)
    assert _WRONG_IS_PALINDROME not in prompt  # ni la sortie du modèle (aucune injection depuis un modèle)


def test_conditioning_is_refused_across_tasks_unknown_or_running_runs(factory) -> None:
    from apex.taskloop.runs import TaskRun, TaskRunStatus, _utcnow

    task_a, task_b = _task(factory), _task(factory)
    service = _service(factory)
    run_a = _run(service, task_a.id)

    with pytest.raises(ValueError, match="autre tâche"):
        service.start(task_id=task_b.id, objective="o", condition_on_run=run_a.id)
    with pytest.raises(ValueError, match="introuvable"):
        service.start(task_id=task_a.id, objective="o", condition_on_run="absente")
    service.repo.create(
        TaskRun(
            id="encore",
            task_id=task_a.id,
            status=TaskRunStatus.RUNNING,
            objective="o",
            execution_mode="bounded",
            requested_by="human",
            limits={},
            started_at=_utcnow(),
        )
    )
    with pytest.raises(ValueError, match="pas terminée"):
        service.start(task_id=task_a.id, objective="o", condition_on_run="encore")


def test_api_accepts_condition_on_run_and_shows_the_provenance(factory, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apex.api import dependencies
    from apex.api.app import create_app
    from tests.api_test_support import TEST_API_TOKEN

    service = _service(factory)
    monkeypatch.setattr(dependencies, "_task_run_service", service)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    client = TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})
    task = client.post("/api/v1/tasks/reference", json={"kind": "is_palindrome"}).json()["data"]
    run1 = client.post(f"/api/v1/tasks/{task['id']}/runs", json={"max_attempts": 1}).json()["data"]
    service.wait(run1["id"], timeout_s=120)

    run2 = client.post(
        f"/api/v1/tasks/{task['id']}/runs", json={"max_attempts": 1, "condition_on_run": run1["id"]}
    ).json()["data"]
    service.wait(run2["id"], timeout_s=120)
    detail = client.get(f"/api/v1/task-runs/{run2['id']}").json()["data"]

    assert detail["condition_on_run"] == run1["id"]
    (experience,) = detail["experiences"]
    assert (
        experience["parent_experience_id"]
        and experience["generation_config"]["generation"]["prompt_suffix"]
    )
    other = client.post("/api/v1/tasks/reference", json={"kind": "is_palindrome"}).json()["data"]
    refused = client.post(f"/api/v1/tasks/{other['id']}/runs", json={"condition_on_run": run1["id"]})
    assert refused.status_code == 422


# -- Tâches contrôlées : bien formées (une solution correcte PASSE, le piège de l'énoncé ÉCHOUE) ------------

_REFERENCE_SOLUTIONS = {
    "rle_encode": (
        "def rle_encode(s):\n    out = []\n    i = 0\n    while i < len(s):\n        j = i\n"
        "        while j < len(s) and s[j] == s[i]:\n            j += 1\n        out.append(f'{s[i]}{j - i}')\n"
        "        i = j\n    return ''.join(out)\n",
        # piège : le compte 1 omis
        "def rle_encode(s):\n    import itertools\n"
        "    return ''.join(k + (str(len(list(g))) if True else '') for k, g in itertools.groupby(s))"
        ".replace('1', '')\n",
    ),
    "count_vowels": (
        "def count_vowels(s):\n    return sum(1 for c in s.lower() if c in 'aeiou')\n",
        "def count_vowels(s):\n    return sum(1 for c in s.lower() if c in 'aeiouy')\n",  # piège : y compté
    ),
    "word_count": (
        "def word_count(s):\n    return len(s.split())\n",
        "def word_count(s):\n    import re\n    return len(re.findall(r'\\w+', s))\n",  # piège : « peut-être » = 2
    ),
    "second_largest": (
        "def second_largest(nums):\n    d = sorted(set(nums))\n    return d[-2] if len(d) >= 2 else None\n",
        "def second_largest(nums):\n    s = sorted(nums)\n    return s[-2] if len(s) >= 2 else None\n",  # piège : doublons
    ),
}


@pytest.mark.parametrize("kind", sorted(_REFERENCE_SOLUTIONS))
def test_each_controlled_task_passes_a_correct_solution_and_fails_its_trap(factory, kind) -> None:
    from apex.policy.model import Subject
    from apex.taskgen.solution_tasks import build_reference_task_spec
    from apex.tasks.model import TaskSplit
    from apex.tasks.service import TaskBankService

    spec = build_reference_task_spec(kind)
    task = TaskBankService(factory).create(
        split=TaskSplit.TRAIN,
        specialty="research",
        title=spec.title,
        description=spec.description,
        command=spec.command,
        created_by=Subject.HUMAN,
        solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer,
    )
    correct, trap = _REFERENCE_SOLUTIONS[kind]
    verdicts = []
    for code in (correct, trap):
        loop = functools.partial(
            run_task_loop,
            candidates=(a_candidate(),),
            registry=TaskLoopRegistry(),
            provider_factory=lambda _c, code=code: _FakeModelProvider(mode="ok", text=code),
        )
        run = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop).start(
            task_id=task.id, objective="o", limits=ONE_ATTEMPT, wait=True
        )
        verdicts.append(run.attempts[0].get("judge_result") or run.failure)
    assert verdicts[0] == "pass", verdicts
    assert verdicts[1] != "pass" and "cas incorrects" in verdicts[1], verdicts
    assert spec.expected_answer not in spec.description
