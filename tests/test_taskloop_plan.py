"""Étape PLAN réelle (`TaskRequest.plan_first`) et événements PLAN honnêtes.

Vraie boucle, vraie sandbox, vrai Judge ; modèle DOUBLURE. Prouve que le plan est demandé au modèle sélectionné,
journalisé, réinjecté dans la génération, et qu'un échec du plan n'arrête pas la tâche. Ne prouve PAS qu'un plan
améliore la réussite d'un vrai modèle (à MESURER).
"""

from __future__ import annotations

import pytest

from apex.errors import ModelProviderError
from apex.metalearner.experience import ExperienceRepository
from apex.taskloop.loop import _PLAN_HEADER, _PLAN_INSTRUCTION
from apex.taskloop.request import TaskLimits
from tests.test_taskloop_adaptation import (
    _kinds,
    _run,
    _Scripted,
    factory,  # noqa: F401  (fixture)
)
from tests.test_taskloop_execution import _CORRECT_IS_PALINDROME

PLAN = "1. Comparer la chaîne à son inverse.\n2. Garder espaces et ponctuation.\n3. Ignorer la casse."
ONE = TaskLimits(max_attempts=1, max_retries=0, max_candidates=1)


def test_the_model_plans_first_and_the_plan_reaches_the_generation(factory) -> None:  # noqa: F811
    task, run = _run(factory, [PLAN, _CORRECT_IS_PALINDROME], limits=ONE, plan_first=True)
    plan_request, code_request = _Scripted.requests
    assert plan_request.prompt == _PLAN_INSTRUCTION + task.description
    assert code_request.prompt == f"{task.description}\n\n{_PLAN_HEADER}\n{PLAN}"
    assert run.status.value == "completed" and run.plan_first is True
    events = _kinds(factory, run.id)
    generated = [e for e in events if e["kind"] == "task.plan.generated"]
    assert len(generated) == 1 and generated[0]["plan"] == PLAN and generated[0]["truncated"] is False
    assert [e["mode"] for e in events if e["kind"] == "task.planning.completed"] == ["model_plan"]
    (experience,) = [e for e in ExperienceRepository(factory).list_by_task(task.id) if e.experiment_ref == run.id]
    assert experience.generation_config["plan"] == PLAN


def test_the_plan_is_kept_across_adapted_retries(factory) -> None:  # noqa: F811
    from tests.test_protocol_v2 import WRONG

    task, _ = _run(factory, [PLAN, WRONG, _CORRECT_IS_PALINDROME], plan_first=True)
    _, first, second = _Scripted.requests
    assert first.prompt == f"{task.description}\n\n{_PLAN_HEADER}\n{PLAN}"
    assert second.prompt.startswith(first.prompt + "\n\nObservations factuelles")


class _PlanFails(_Scripted):
    def generate(self, request):
        if request.prompt.startswith(_PLAN_INSTRUCTION):
            type(self).requests.append(request)
            raise ModelProviderError("plan", endpoint_url=request.endpoint.url, detail="panne simulée")
        return super().generate(request)


def test_a_failed_plan_is_logged_and_never_blocks_nor_invents_a_plan(factory, monkeypatch) -> None:  # noqa: F811
    import tests.test_taskloop_adaptation as adaptation

    monkeypatch.setattr(adaptation, "_Scripted", _PlanFails)
    _PlanFails.requests = []
    task, run = adaptation._run(factory, [_CORRECT_IS_PALINDROME], limits=ONE, plan_first=True)
    assert run.status.value == "completed"
    assert _PlanFails.requests[-1].prompt == task.description  # aucun plan inventé
    failed = [e for e in _kinds(factory, run.id) if e["kind"] == "task.plan.failed"]
    assert len(failed) == 1 and "panne simulée" in failed[0]["reason"]


def test_without_plan_first_planning_is_only_prompt_composition(factory) -> None:  # noqa: F811
    task, run = _run(factory, [_CORRECT_IS_PALINDROME], limits=ONE)
    assert [r.prompt for r in _Scripted.requests] == [task.description]
    events = _kinds(factory, run.id)
    assert [e["mode"] for e in events if e["kind"] == "task.planning.completed"] == ["prompt_composition"]
    assert not [e for e in events if e["kind"].startswith("task.plan.")]


@pytest.mark.parametrize("plan", ["", "x" * 5000])
def test_an_empty_plan_is_refused_and_a_long_plan_is_bounded(factory, plan) -> None:  # noqa: F811
    task, run = _run(factory, [plan, _CORRECT_IS_PALINDROME], limits=ONE, plan_first=True)
    events = _kinds(factory, run.id)
    if not plan:
        assert [e for e in events if e["kind"] == "task.plan.failed"]
        assert _Scripted.requests[1].prompt == task.description
    else:
        (generated,) = [e for e in events if e["kind"] == "task.plan.generated"]
        assert generated["truncated"] is True and len(generated["plan"]) == 2000
