"""DIAGNOSTIC -> CORRECTION entre deux tentatives d'une même TaskRun (`TaskRequest.retry_adaptation`).

Avant le 05/10/2026, la politique de reprise annonçait « feedback du judge incorporé au contexte de la
tentative suivante » alors que la boucle relançait EXACTEMENT le même prompt. Ces tests prouvent le
comportement réel : vraie boucle, vraie sandbox, vrai Judge, modèle DOUBLURE qui enregistre ce qu'il reçoit
(composition prouvée, jamais un gain sur un vrai modèle).
"""

from __future__ import annotations

import functools
from typing import ClassVar

import pytest

from apex.agents.runtime import AgentRuntime
from apex.db.engine import session_scope
from apex.db.repositories import EventRepository
from apex.metalearner.experience import ExperienceRepository
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits, TaskRequest
from apex.taskloop.runs import TaskRunService
from tests.test_protocol_v2 import LEAKY, WRONG
from tests.test_task_runs import _task
from tests.test_taskloop_execution import (
    _CORRECT_IS_PALINDROME,
    _FakeModelProvider,
    a_candidate,
    a_pool,
    sqlite_session_factory,
)

THREE = TaskLimits(max_attempts=3, max_retries=2, max_candidates=1)


class _Scripted(_FakeModelProvider):
    """Renvoie, appel après appel, les codes de `script` (le dernier se répète) et enregistre les requêtes."""

    script: ClassVar[list[str]] = []
    requests: ClassVar[list] = []

    def generate(self, request):
        type(self).requests.append(request)
        index = min(len(type(self).requests) - 1, len(type(self).script) - 1)
        self.text = type(self).script[index]
        return super().generate(request)


@pytest.fixture
def factory():
    _Scripted.requests = []
    return sqlite_session_factory()


def _run(factory, codes, *, limits=THREE, **kwargs):
    _Scripted.script = list(codes)
    task = _task(factory)
    loop = functools.partial(
        run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _Scripted(mode="ok"),
    )
    service = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)
    run = service.start(task_id=task.id, objective="o", limits=limits, wait=True, **kwargs)
    return task, run


def _kinds(factory, trace_id):
    with session_scope(factory) as session:
        rows = EventRepository(session).recent(limit=1000)
    return [r.context for r in reversed(rows) if r.context.get("trace_id") == trace_id]


def test_the_retry_receives_the_facts_of_the_failed_attempt_and_can_then_succeed(factory) -> None:
    task, run = _run(factory, [WRONG, _CORRECT_IS_PALINDROME], base_temperature=0.4)
    first, second = _Scripted.requests
    assert first.prompt == task.description
    assert second.prompt.startswith(task.description + "\n\nObservations factuelles")
    assert "cas en échec : 3 sur 12" in second.prompt and "résultats incorrects" in second.prompt
    assert second.temperature == first.temperature == 0.4  # température en cours conservée
    assert run.status.value == "completed" and len(run.attempts) == 2

    exp1, exp2 = sorted(
        (e for e in ExperienceRepository(factory).list_by_task(task.id) if e.experiment_ref == run.id),
        key=lambda e: e.generation_number,
    )
    assert exp2.parent_experience_id == exp1.id and exp2.generation_number == exp1.generation_number + 1
    assert exp2.generation_config["adaptation"] == "in_run"
    assert exp2.generation_config["source_eval_run_id"] == exp1.eval_run_id
    events = [e for e in _kinds(factory, run.id) if e["kind"] == "strategy.adapted"]
    assert len(events) == 1 and events[0]["after_attempt"] == 1


def test_each_retry_uses_the_latest_failure_never_an_older_one(factory) -> None:
    _run(factory, [WRONG, LEAKY, LEAKY])
    _, second, third = _Scripted.requests
    assert "3 sur 12" in second.prompt and "KeyError" not in second.prompt
    assert "4 sur 12" in third.prompt and "(KeyError)" in third.prompt and "3 sur 12" not in third.prompt


def test_the_adapted_prompt_never_carries_hidden_inputs_messages_or_previous_code(factory) -> None:
    task, _ = _run(factory, [LEAKY, LEAKY], limits=TaskLimits(max_attempts=2, max_retries=1, max_candidates=1))
    suffix = _Scripted.requests[1].prompt.removeprefix(task.description)
    for forbidden in ("nurses run", "No lemon", "ab a", "aB ba", "KeyError(", "raise", "def ", "indices", "["):
        assert forbidden not in suffix, forbidden


def test_adaptation_none_keeps_the_historical_identical_retry(factory) -> None:
    task, run = _run(factory, [WRONG, WRONG, WRONG], retry_adaptation="none")
    assert [r.prompt for r in _Scripted.requests] == [task.description] * len(_Scripted.requests)
    assert not [e for e in _kinds(factory, run.id) if e["kind"] == "strategy.adapted"]


def test_a_single_attempt_run_is_never_adapted(factory) -> None:
    """Les protocoles RUN 1 -> RUN 2 (une tentative) ne sont pas touchés par l'adaptation entre tentatives."""
    task, run = _run(factory, [WRONG], limits=TaskLimits(max_attempts=1, max_retries=0, max_candidates=1))
    assert [r.prompt for r in _Scripted.requests] == [task.description]
    assert not [e for e in _kinds(factory, run.id) if e["kind"] in ("strategy.adapted", "strategy.unchanged")]


def test_an_uncovered_failure_category_yields_no_adaptation() -> None:
    """Délai dépassé, infrastructure, ... : aucun fait autorisé -> la stratégie en cours est gardée (la boucle
    émet alors `strategy.unchanged` avec la raison)."""
    from apex.metalearner.failure_taxonomy import FailureCategory
    from apex.taskloop.loop import _adapted_strategy, _Strategy

    for category in (FailureCategory.TIMEOUT, FailureCategory.INFRASTRUCTURE_FAILURE, FailureCategory.SUCCESS):

        class _Exp:
            failure_category = category

        assert _adapted_strategy(_Strategy(conditioned=False), _Exp(), object(), 1) is None


def test_the_request_rejects_an_unknown_adaptation() -> None:
    with pytest.raises(ValueError):
        TaskRequest(task_id="t", objective="o", retry_adaptation="libre")


def test_condition_on_latest_learns_from_the_last_finished_run_of_the_same_task(factory) -> None:
    """`condition_on_run="latest"` : résolu vers la dernière TaskRun terminée de CETTE tâche, enregistré par
    son identifiant réel ; refusé s'il n'existe aucune exécution terminée."""
    from tests.test_protocol_v2 import T0

    _Scripted.script = [WRONG]
    task = _task(factory)
    loop = functools.partial(
        run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _Scripted(mode="ok"),
    )
    service = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)
    one = TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)
    with pytest.raises(ValueError):
        service.start(task_id=task.id, objective="o", limits=one, wait=True, condition_on_run="latest")
    first = service.start(task_id=task.id, objective="o", limits=one, wait=True, base_temperature=T0)
    second = service.start(task_id=task.id, objective="o", limits=one, wait=True, condition_on_run="latest",
                           strategy="failure_facts", base_temperature=T0)
    assert second.condition_on_run == first.id
    assert "cas en échec : 3 sur 12" in _Scripted.requests[-1].prompt
