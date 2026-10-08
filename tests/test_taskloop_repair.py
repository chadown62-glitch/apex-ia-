"""Cycle ÉCHEC -> OBSERVATION -> DIAGNOSTIC -> CHANGEMENT DE STRATÉGIE -> RETRY (`retry_adaptation="diagnose_repair"`).

Vraie boucle, vraie sandbox, vrai Judge ; modèle DOUBLURE qui enregistre ce qu'il reçoit. Ces tests prouvent la
MÉCANIQUE (ce que voit chaque appel, ce qui est tracé, ce qui n'est jamais montré) — jamais un gain sur un vrai
modèle, qui se MESURE (docs/VALIDATION_REELLE.md §6).
"""

from __future__ import annotations

import functools
import json

from apex.agents.runtime import AgentRuntime
from apex.metalearner.experience import ExperienceRepository
from apex.policy.model import Subject
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits
from apex.taskloop.runs import TaskRunService
from apex.tasks.model import TaskSplit
from apex.tasks.service import TaskBankService
from apex.tasks.solution_tasks import build_user_function_task_spec
from tests.test_protocol_v2 import LEAKY, WRONG
from tests.test_taskloop_adaptation import THREE, _kinds, _run, _Scripted, factory  # noqa: F401
from tests.test_taskloop_execution import _CORRECT_IS_PALINDROME, a_candidate, a_pool

DIAGNOSIS = "Le code retire espaces et ponctuation alors que l'énoncé impose de les garder."


def _changed(factory, run):  # noqa: F811
    return [e for e in _kinds(factory, run.id) if e["kind"] == "strategy.changed"]


def test_the_retry_sees_its_own_code_the_observations_and_its_own_diagnosis(factory) -> None:  # noqa: F811
    task, run = _run(factory, [WRONG, DIAGNOSIS, _CORRECT_IS_PALINDROME], retry_adaptation="diagnose_repair")
    first, diagnosis_call, repair_call = _Scripted.requests
    assert run.status.value == "completed" and len(run.attempts) == 2
    # DIAGNOSTIC : appel court, sur le code échoué et les faits AUTORISÉS — jamais un cas caché.
    assert diagnosis_call.max_tokens == 120 and "cause la plus probable" in diagnosis_call.prompt
    assert WRONG.strip() in diagnosis_call.prompt and "cas en échec : 3 sur 12" in diagnosis_call.prompt
    # RÉPARATION : son code, les observations et SON diagnostic, puis la consigne de format.
    assert repair_call.prompt.startswith(task.description)
    assert WRONG.strip() in repair_call.prompt and f"Cause probable (ton propre diagnostic) : {DIAGNOSIS}" in repair_call.prompt
    hidden = json.loads(task.expected_answer)
    for prompt in (diagnosis_call.prompt, repair_call.prompt):
        assert not any(isinstance(v, str) and len(v) > 3 and repr(v) in prompt for v in hidden)
    (changed,) = _changed(factory, run)
    assert changed["strategy_before"]["mode"] == "initial" and changed["strategy_after"]["mode"] == "repair"
    assert changed["changed"] is True and changed["diagnosis"] == DIAGNOSIS
    (outcome,) = [e for e in _kinds(factory, run.id) if e["kind"] == "strategy.outcome"]
    assert outcome["code_changed"] is True and outcome["improvement_delta"] > 0
    # EXPÉRIENCE : diagnostic, stratégie, observation et gain de score enregistrés pour une décision future.
    second = [e for e in ExperienceRepository(factory).list_by_task(task.id) if e.experiment_ref == run.id
              and e.generation_config.get("adaptation") == "in_run_repair"]
    assert second and second[0].generation_config["diagnosis"] == DIAGNOSIS
    assert second[0].generation_config["repair"]["mode"] == "repair" and second[0].improvement_delta > 0


def test_a_repair_without_progress_becomes_a_rewrite_and_is_not_stopped_as_no_progress(factory) -> None:  # noqa: F811
    task, run = _run(factory, [WRONG, DIAGNOSIS, WRONG, "Toujours la même erreur.", _CORRECT_IS_PALINDROME],
                     retry_adaptation="diagnose_repair", base_temperature=0.4)
    assert run.status.value == "completed" and len(run.attempts) == 3  # 3e tentative jouée, pas « no_progress »
    rewrite_call = _Scripted.requests[4]
    assert "NOUVELLE solution" in rewrite_call.prompt and WRONG.strip() not in rewrite_call.prompt
    assert DIAGNOSIS in rewrite_call.prompt  # les causes déjà identifiées sont rappelées
    assert rewrite_call.temperature > 0.4  # stratégie différente : exploration relevée
    first, second = _changed(factory, run)
    assert second["strategy_before"]["mode"] == "repair" and second["strategy_after"]["mode"] == "rewrite"
    outcomes = [e for e in _kinds(factory, run.id) if e["kind"] == "strategy.outcome"]
    assert outcomes[0]["code_changed"] is False  # le modèle a resoumis le même code : mesuré, pas supposé


def test_an_exception_message_never_reaches_the_diagnosis(factory) -> None:  # noqa: F811
    task, run = _run(factory, [LEAKY, DIAGNOSIS, _CORRECT_IS_PALINDROME], retry_adaptation="diagnose_repair")
    diagnosis_call = _Scripted.requests[1]
    assert "KeyError" in diagnosis_call.prompt  # la CLASSE native est un fait autorisé
    hidden = [v for v in json.loads(task.expected_answer) if isinstance(v, str)]
    assert not any(f"KeyError('{v}')" in diagnosis_call.prompt for v in hidden)  # jamais le message


def test_public_examples_of_the_statement_are_run_and_observed(factory) -> None:  # noqa: F811
    spec = build_user_function_task_spec(
        function="double", description="Écris `double(x)` qui double x. Exemples : `double(2) == 4`, `double(0) == 0`.",
        cases=[{"args": [5], "expected": 10}, {"args": [-3], "expected": -6}])
    task = TaskBankService(factory).create(
        split=TaskSplit.TRAIN, specialty="research", title=spec.title, description=spec.description,
        command=spec.command, created_by=Subject.HUMAN, solution_harness=spec.solution_harness,
        expected_answer=spec.expected_answer)
    _Scripted.script = ["def double(x):\n    return x + 2\n", "Il ajoute 2 au lieu de multiplier.",
                        "def double(x):\n    return 2 * x\n"]
    loop = functools.partial(run_task_loop, candidates=(a_candidate(),), registry=TaskLoopRegistry(),
                             provider_factory=lambda _c: _Scripted(mode="ok"))
    run = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop).start(
        task_id=task.id, objective="o", limits=THREE, wait=True, retry_adaptation="diagnose_repair")
    diagnosis_call = _Scripted.requests[1]
    assert "double(2) : attendu 4, obtenu 4 — OK" in diagnosis_call.prompt
    assert "double(0) : attendu 0, obtenu 2 — ÉCHEC" in diagnosis_call.prompt
    assert "10" not in diagnosis_call.prompt.split("Ce qui a été observé")[1]  # cas caché (5 -> 10) jamais montré
    (changed,) = _changed(factory, run)
    assert changed["observation"]["public_examples"] == 2 and changed["observation"]["public_examples_failed"] == 1
    assert run.status.value == "completed"


def test_other_adaptations_keep_their_exact_behaviour(factory) -> None:  # noqa: F811
    """Les bras mesurés (R, C) ne changent pas : aucun diagnostic, aucune réparation, même arrêt « no_progress »."""
    _, run = _run(factory, [WRONG, WRONG, WRONG], retry_adaptation="failure_facts")
    assert len(_Scripted.requests) == 2 and run.stop_reason == "no_progress"
    assert not _changed(factory, run)


def test_the_limits_of_a_run_are_unchanged(factory) -> None:  # noqa: F811
    _, run = _run(factory, [WRONG, DIAGNOSIS, WRONG, DIAGNOSIS, WRONG, DIAGNOSIS],
                  retry_adaptation="diagnose_repair", limits=TaskLimits(max_attempts=2, max_retries=1, max_candidates=1))
    assert len(run.attempts) == 2 and run.status.value == "failed"


def test_the_exploration_control_changes_only_the_temperature_on_the_repair_schedule(factory) -> None:  # noqa: F811
    """Témoin `explore` (§6.6) : même prompt, aucun diagnostic ; température de la 2e tentative inchangée, celle de
    la 3e = celle d'une réécriture de `diagnose_repair` (base + 0,3)."""
    # Deux échecs de catégories différentes : le garde-fou « no_progress » (patience 2) laisse jouer la 3e tentative.
    task, run = _run(factory, [WRONG, LEAKY, _CORRECT_IS_PALINDROME], retry_adaptation="explore", base_temperature=0.4)
    assert len(_Scripted.requests) == len(run.attempts)  # aucun appel de diagnostic
    prompts = {r.prompt for r in _Scripted.requests}
    assert prompts == {task.description}  # le prompt ne change jamais
    temps = [r.temperature for r in _Scripted.requests]
    assert len(temps) == 3 and temps[:2] == [0.4, 0.4] and abs(temps[2] - 0.7) < 1e-9
    assert run.status.value == "completed"
    explored = [e for e in _kinds(factory, run.id) if e["kind"] == "strategy.explored"]
    assert explored and all(e["prompt_changed"] is False for e in explored)
    assert not _changed(factory, run)  # jamais un `strategy.changed` (pas de diagnostic)
