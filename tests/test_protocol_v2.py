"""Protocole v2 (docs/PROTOCOLE_V2.md) : bras A, B, C réellement comparables, faits de C sans fuite.

Vraie boucle, vraie base, vraie sandbox, vrai Judge ; le modèle est une doublure qui ENREGISTRE chaque
`GenerationRequest` reçue — ces tests prouvent la COMPOSITION des bras, jamais un effet sur un vrai modèle.
"""

from __future__ import annotations

import dataclasses
import functools
import json
from typing import ClassVar

import pytest

from apex.agents.runtime import AgentRuntime
from apex.metalearner.adaptive_solution_loop import _DEFAULT_BASE_TEMPERATURE, _INCORRECT_SOLUTION_HINT
from apex.metalearner.experience import ExperienceRepository
from apex.metalearner.failure_facts import (
    ALLOWED_FIELDS,
    PROMPT_VERSION,
    FailureFacts,
    extract_failure_facts,
    render_failure_facts,
)
from apex.metalearner.failure_taxonomy import FailureCategory
from apex.taskgen.solution_tasks import build_palindrome_task_spec
from apex.taskloop.loop import run_task_loop
from apex.taskloop.registry import TaskLoopRegistry
from apex.taskloop.request import TaskLimits, TaskRequest
from apex.taskloop.runs import TaskRunService
from tests.test_task_runs import _task
from tests.test_taskloop_execution import _FakeModelProvider, a_candidate, a_pool, sqlite_session_factory

T0 = 0.7
ONE = TaskLimits(max_attempts=1, max_retries=0)
#: Faux : retire espaces et ponctuation (contre l'énoncé) — 3/12 cas incorrects, aucune exception.
WRONG = (
    "def is_palindrome(s):\n    t = ''.join(c.lower() for c in s if c.isalnum())\n    return t == t[::-1]\n"
)
#: Lève une exception DONT LE MESSAGE CONTIENT L'ENTRÉE CACHÉE sur les entrées à espace.
LEAKY = (
    "def is_palindrome(s):\n    if ' ' in s:\n        raise KeyError(s)\n"
    "    return s.lower() == s.lower()[::-1]\n"
)


class _Recording(_FakeModelProvider):
    requests: ClassVar[list] = []

    def generate(self, request):
        type(self).requests.append(request)
        return super().generate(request)


@pytest.fixture
def factory():
    _Recording.requests = []
    return sqlite_session_factory()


def _service(factory, code: str) -> TaskRunService:
    loop = functools.partial(
        run_task_loop,
        candidates=(a_candidate(),),
        registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _Recording(mode="ok", text=code),
    )
    return TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)


def _run(service, task_id, **kwargs):
    return service.start(task_id=task_id, objective="o", limits=ONE, wait=True, **kwargs)


def _only_experience(factory, task_id, run_id):
    (experience,) = [
        e for e in ExperienceRepository(factory).list_by_task(task_id) if e.experiment_ref == run_id
    ]
    return experience


def _arms(factory, run1_code: str):
    """RUN 1 (T0) puis A, B, C conditionnés sur ce même RUN 1 ; renvoie les requêtes reçues par le modèle."""
    task = _task(factory)
    service = _service(factory, run1_code)
    run1 = _run(service, task.id, base_temperature=T0)
    _run(service, task.id, base_temperature=T0)  # A
    _run(service, task.id, condition_on_run=run1.id, strategy="rule")  # B
    run_c = _run(
        service, task.id, condition_on_run=run1.id, strategy="failure_facts", base_temperature=T0
    )  # C
    req1, req_a, req_b, req_c = _Recording.requests[-4:]
    return task, run1, run_c, req1, req_a, req_b, req_c


# -- comparabilité -------------------------------------------------------------------------------------------


def test_run1_and_arm_a_use_the_explicit_base_temperature_and_the_bare_description(factory) -> None:
    task, run1, _, req1, req_a, _, _ = _arms(factory, WRONG)
    for request in (req1, req_a):
        assert request.prompt == task.description and request.temperature == T0
    assert _only_experience(factory, task.id, run1.id).generation_config == {
        "generation": {"temperature": T0}
    }


def test_a_and_c_differ_only_by_the_allowed_facts_suffix(factory) -> None:
    _, _, _, _, req_a, _, req_c = _arms(factory, WRONG)
    differing = {
        f.name for f in dataclasses.fields(req_a) if getattr(req_a, f.name) != getattr(req_c, f.name)
    }
    assert differing == {"prompt"}
    assert req_c.temperature == req_a.temperature == T0
    suffix = req_c.prompt.removeprefix(req_a.prompt + "\n\n")
    assert req_c.prompt == f"{req_a.prompt}\n\n{suffix}" and suffix.startswith("Observations factuelles")


def test_arm_b_is_the_unchanged_rule(factory) -> None:
    task, _, _, _, _, req_b, _ = _arms(factory, WRONG)
    assert req_b.prompt == f"{task.description}\n\n{_INCORRECT_SOLUTION_HINT}"
    assert req_b.temperature == pytest.approx(_DEFAULT_BASE_TEMPERATURE + 0.4)


def test_arm_c_temperature_is_exactly_the_requested_one(factory) -> None:
    task = _task(factory)
    service = _service(factory, WRONG)
    run1 = _run(service, task.id, base_temperature=T0)
    for value in (0.0, 0.7, 1.3):
        _run(service, task.id, condition_on_run=run1.id, strategy="failure_facts", base_temperature=value)
        assert _Recording.requests[-1].temperature == value


# -- faits de C : contenu, provenance, absence de fuite -------------------------------------------------------


def test_arm_c_transmits_the_allowed_facts_with_their_provenance(factory) -> None:
    task, run1, run_c, _, _, _, req_c = _arms(factory, WRONG)
    exp1 = _only_experience(factory, task.id, run1.id)
    exp_c = _only_experience(factory, task.id, run_c.id)
    config = exp_c.generation_config
    assert set(config["failure_facts"]) == set(ALLOWED_FIELDS)
    assert config["failure_facts"] == {
        "failure_category": "incorrect_solution",
        "failed_cases": 3,
        "total_cases": 12,
        "exception_class_count": 0,
        "exception_classes": [],
        "script_terminated": True,
        "exit_code": 0,
    }
    assert config["prompt_version"] == PROMPT_VERSION and config["source_eval_run_id"] == exp1.eval_run_id
    assert exp_c.parent_experience_id == exp1.id
    assert "cas en échec : 3 sur 12" in req_c.prompt and "résultats incorrects" in req_c.prompt
    assert run_c.strategy == "failure_facts" and run_c.base_temperature == T0


def test_no_hidden_input_expected_answer_index_message_or_previous_code_reaches_the_prompt(factory) -> None:
    task, _, run_c, _, req_a, _, req_c = _arms(factory, LEAKY)
    suffix = req_c.prompt.removeprefix(req_a.prompt + "\n\n")
    spec = build_palindrome_task_spec()
    # Entrées cachées assez distinctives pour qu'une occurrence soit une fuite (« a » apparaît partout en
    # français) ; toutes celles qui lèvent l'exception de LEAKY (avec espace) en font partie.
    hidden_inputs = [
        s
        for s in json.loads(
            spec.solution_harness.split("_APEX_INPUTS = ")[1].splitlines()[0].replace("'", '"')
        )
        if len(s) >= 4
    ]
    assert {"ab a", "nurses run", "No lemon, no melon", "aB ba"} <= set(hidden_inputs)
    assert "KeyError" in suffix  # la CLASSE seule est autorisée…
    for forbidden in [
        *hidden_inputs,
        spec.expected_answer,
        "indices",
        "[",
        "raise",
        LEAKY,
        "KeyError(",
        "def ",
    ]:
        assert forbidden not in suffix, (
            forbidden
        )  # … jamais le message (qui contient l'entrée), ni le reste
    facts = _only_experience(factory, task.id, run_c.id).generation_config["failure_facts"]
    assert facts["exception_classes"] == ["KeyError"] and facts["failure_category"] == "incorrect_solution"


def test_facts_are_observations_never_instructions() -> None:
    text = render_failure_facts(FailureFacts("incorrect_solution", 2, 8, 1, ("IndexError",), True, 0))
    lowered = text.lower()
    for imperative in ("corrige", "vérifie", "relis", "assure", "attention", "doit", "il faut"):
        assert imperative not in lowered


def test_extraction_keeps_only_class_names_and_covers_only_failed_candidate_categories() -> None:
    from apex.evalrunner.model import EvalMethod, EvalRun
    from apex.oracles.model import OracleVerdict
    from apex.oracles.result_contract import RESULT_LINE_PREFIX
    from apex.sandbox.model import ExecutionStatus

    # Message porteur d'une entrée cachée ; repr illisible ; classe créée par le candidat AU NOM d'une entrée
    # cachée (`type("racecar", (Exception,), {})`) : seuls des noms de classes NATIVES peuvent sortir.
    results = [
        1, {"error": "KeyError('secret hidden input')"}, {"error": "<weird repr>"}, {"error": "racecar()"}, 3,
    ]
    run = EvalRun(
        id="r",
        task_id="t",
        method=EvalMethod.EXPERIMENTATION,
        oracle_verdict=OracleVerdict.FAIL,
        oracle_detail="3/5 cas incorrects (indices [1, 2, 3]), dont 3 exception(s) levée(s) par le candidat",
        exit_code=0,
        elapsed_s=0.1,
        facts={"candidate_execution": {"stdout": RESULT_LINE_PREFIX + json.dumps({"results": results})}},
        created_by="core",
        clone_id="c",
        sandbox_status=ExecutionStatus.SUCCESS,
        model_id="m",
    )
    facts = extract_failure_facts(run, FailureCategory.INCORRECT_SOLUTION)
    assert facts.exception_classes == ("KeyError", "classe non standard") and facts.exception_class_count == 3
    assert (facts.failed_cases, facts.total_cases) == (3, 5)
    for leaked in ("secret", "racecar", "weird"):
        assert leaked not in json.dumps(facts.to_dict()) and leaked not in render_failure_facts(facts)
    for category in (
        FailureCategory.SUCCESS,
        FailureCategory.TIMEOUT,
        FailureCategory.INFRASTRUCTURE_FAILURE,
        FailureCategory.GENERATION_FAILURE,
    ):
        assert extract_failure_facts(run, category) is None


def test_the_rendered_facts_fit_the_prompt_suffix_bound() -> None:
    from apex.genome.clone_config import _MAX_PROMPT_SUFFIX_CHARS

    worst = FailureFacts("invalid_candidate", 9999, 9999, 99, tuple("X" * 40 for _ in range(5)), False, 255)
    assert len(render_failure_facts(worst)) <= _MAX_PROMPT_SUFFIX_CHARS


def test_arm_c_transmits_nothing_when_the_run1_category_is_not_covered(factory) -> None:
    from tests.test_taskloop_execution import _CORRECT_IS_PALINDROME

    task, _, run_c, _, req_a, _, req_c = _arms(factory, _CORRECT_IS_PALINDROME)  # RUN 1 réussi
    assert req_c.prompt == req_a.prompt == task.description and req_c.temperature == T0
    config = _only_experience(factory, task.id, run_c.id).generation_config
    assert config == {"generation": {"temperature": T0}} and "failure_facts" not in config


# -- garde-fous ------------------------------------------------------------------------------------------------


def test_requests_are_validated() -> None:
    with pytest.raises(ValueError):
        TaskRequest(
            task_id="t", objective="o", strategy="failure_facts", base_temperature=T0
        )  # C sans RUN 1
    with pytest.raises(ValueError):
        TaskRequest(
            task_id="t", objective="o", strategy="failure_facts", condition_on_run="r"
        )  # T implicite
    with pytest.raises(ValueError):
        TaskRequest(task_id="t", objective="o", strategy="inventée")
    with pytest.raises(ValueError):
        TaskRequest(task_id="t", objective="o", base_temperature=2.5)


def test_the_judge_still_decides_arm_c_success(factory) -> None:
    """Les faits n'influencent jamais le verdict : une solution fausse en C échoue, une juste réussit."""
    task = _task(factory)
    run1 = _run(_service(factory, WRONG), task.id, base_temperature=T0)
    still_wrong = _run(
        _service(factory, WRONG),
        task.id,
        condition_on_run=run1.id,
        strategy="failure_facts",
        base_temperature=T0,
    )
    assert still_wrong.attempts[0]["judge_result"] is None and still_wrong.status.value in (
        "failed",
        "blocked",
    )
    from tests.test_taskloop_execution import _CORRECT_IS_PALINDROME

    right = _run(
        _service(factory, _CORRECT_IS_PALINDROME),
        task.id,
        condition_on_run=run1.id,
        strategy="failure_facts",
        base_temperature=T0,
    )
    assert right.attempts[0]["judge_result"] == "pass"


def test_api_validates_v2_parameters(factory, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from apex.api import dependencies
    from apex.api.app import create_app
    from tests.api_test_support import TEST_API_TOKEN

    monkeypatch.setattr(dependencies, "_task_run_service", _service(factory, WRONG))
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    client = TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"})
    task = client.post("/api/v1/tasks/reference", json={"kind": "is_palindrome"}).json()["data"]
    url = f"/api/v1/tasks/{task['id']}/runs"
    assert client.post(url, json={"strategy": "failure_facts"}).status_code == 422
    assert client.post(url, json={"base_temperature": 3}).status_code == 422
    assert client.post(url, json={"strategy": "inventée"}).status_code == 422
