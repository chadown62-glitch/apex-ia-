"""PLAN ADAPTATIF (`plan_policy="auto"`, docs/VALIDATION_REELLE.md §6.4) : EXPÉRIENCE -> DÉCISION.

Le plan n'est rédigé que si les exécutions PASSÉES de tâches similaires réussissent davantage AVEC un plan
réellement injecté (au moins 3 de chaque côté) ; sinon il est contourné, et la décision est journalisée avec ses
preuves. Vraie boucle, vraie mémoire, vraie sandbox, vrai Judge ; modèle DOUBLURE. Prouve la RÈGLE, jamais
qu'un vrai modèle y gagne (à MESURER).
"""

from __future__ import annotations

import pytest

from apex.taskloop.loop import _PLAN_INSTRUCTION
from apex.taskloop.request import TaskLimits, TaskRequest
from tests.test_protocol_v2 import WRONG
from tests.test_taskloop_adaptation import _kinds, _run, _Scripted, factory  # noqa: F401
from tests.test_taskloop_execution import _CORRECT_IS_PALINDROME

PLAN = "1. Comparer la chaîne à son inverse.\n2. Garder espaces et ponctuation.\n3. Ignorer la casse."
ONE = TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)


def _fresh(factory, codes, **kwargs):  # noqa: F811
    _Scripted.requests = []  # le script est indexé par le nombre d'appels de CETTE exécution
    return _run(factory, codes, limits=ONE, **kwargs)


def _decided(factory, run):  # noqa: F811
    (event,) = [e for e in _kinds(factory, run.id) if e["kind"] == "task.plan.decided"]
    return event


def _history(factory, *, with_plan_succeeds: bool, without_plan_succeeds: bool) -> None:  # noqa: F811
    for _ in range(3):
        _fresh(factory, [PLAN, _CORRECT_IS_PALINDROME if with_plan_succeeds else WRONG], plan_first=True)
        _fresh(factory, [_CORRECT_IS_PALINDROME if without_plan_succeeds else WRONG])


def test_without_evidence_the_plan_is_bypassed_and_the_decision_is_logged(factory) -> None:  # noqa: F811
    _, run = _fresh(factory, [_CORRECT_IS_PALINDROME], plan_policy="auto")
    assert len(_Scripted.requests) == 1 and not _Scripted.requests[0].prompt.startswith(_PLAN_INSTRUCTION)
    event = _decided(factory, run)
    assert event["decision"] == "bypass" and event["reason"].startswith("preuve insuffisante")
    assert run.status.value == "completed" and run.plan_policy == "auto"
    assert [e["mode"] for e in _kinds(factory, run.id) if e["kind"] == "task.planning.started"] == ["adaptive"]


def test_the_plan_is_used_when_similar_runs_succeed_more_with_it(factory) -> None:  # noqa: F811
    _history(factory, with_plan_succeeds=True, without_plan_succeeds=False)
    _, run = _fresh(factory, [PLAN, _CORRECT_IS_PALINDROME], plan_policy="auto")
    assert _Scripted.requests[0].prompt.startswith(_PLAN_INSTRUCTION)  # le plan est réellement demandé
    event = _decided(factory, run)
    assert event["decision"] == "plan"
    assert event["with_plan"] == {"runs": 3, "completed": 3} and event["without_plan"] == {"runs": 3, "completed": 0}
    assert len(event["evidence"]) == 6 and run.status.value == "completed"


def test_a_plan_that_does_not_do_better_is_bypassed_because_it_costs_more(factory) -> None:  # noqa: F811
    _history(factory, with_plan_succeeds=True, without_plan_succeeds=True)
    _, run = _fresh(factory, [_CORRECT_IS_PALINDROME], plan_policy="auto")
    event = _decided(factory, run)
    assert event["decision"] == "bypass" and "ne fait pas mieux" in event["reason"]
    assert len(_Scripted.requests) == 1


def test_evidence_counts_plans_really_injected_not_only_requested(factory, monkeypatch) -> None:  # noqa: F811
    """Un plan demandé mais en échec (aucun plan injecté) compte du côté « sans plan »."""
    import apex.taskloop.loop as loop

    monkeypatch.setattr(loop, "_generate_plan", lambda *a, **k: None)
    for _ in range(3):
        _fresh(factory, [_CORRECT_IS_PALINDROME], plan_first=True)
    monkeypatch.undo()
    _, run = _fresh(factory, [_CORRECT_IS_PALINDROME], plan_policy="auto")
    event = _decided(factory, run)
    assert event["with_plan"]["runs"] == 0 and event["without_plan"]["runs"] == 3


def test_the_requested_policy_keeps_its_exact_behaviour(factory) -> None:  # noqa: F811
    _, planned = _fresh(factory, [PLAN, _CORRECT_IS_PALINDROME], plan_first=True)
    assert _Scripted.requests[0].prompt.startswith(_PLAN_INSTRUCTION)
    _, plain = _fresh(factory, [_CORRECT_IS_PALINDROME])
    assert len(_Scripted.requests) == 1
    for run in (planned, plain):
        assert not [e for e in _kinds(factory, run.id) if e["kind"] == "task.plan.decided"]
        assert run.plan_policy == "request"


def test_an_unknown_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="politique de plan"):
        TaskRequest(task_id="t", objective="o", plan_policy="toujours")


def test_mission_options_validate_the_policy() -> None:
    from types import SimpleNamespace

    from apex.missions.task_options import normalize_task_options

    task = SimpleNamespace(effective_kind=None)
    assert normalize_task_options({"plan_policy": "auto"}, task=task)["plan_policy"] == "auto"
    with pytest.raises(ValueError, match="plan_policy"):
        normalize_task_options({"plan_policy": "parfois"}, task=task)
