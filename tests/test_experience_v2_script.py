"""Script du protocole v2 (scripts/apex_experience_v2.py) : plan pré-enregistré, contrôles de validité, analyse.

Les tests d'intégration passent par la VRAIE API, la vraie boucle, la vraie sandbox et le vrai Judge, avec un
modèle DOUBLURE : ils prouvent l'enchaînement (calibration, carré latin, arrêt, contrôles, analyse), jamais un
résultat expérimental — aucun chiffre produit ici ne mesure un effet des bras.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

from apex.metalearner.adaptive_solution_loop import _INCORRECT_SOLUTION_HINT

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "apex_experience_v2.py"
_spec = importlib.util.spec_from_file_location("apex_experience_v2", _PATH)
v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v2)


# -- plan pré-enregistré ---------------------------------------------------------------------------------


def test_preregistered_constants_are_frozen() -> None:
    assert (v2.T0, v2.DRAWS, v2.KEEP_MIN, v2.KEEP_MAX) == (0.7, 8, 1, 6)
    assert (v2.MAX_ELIGIBLE, v2.MAX_RUN1, v2.MIN_DISCORDANT, v2.MAX_INFRA_SHARE, v2.ALPHA) == (
        60,
        240,
        20,
        0.10,
        0.05,
    )
    assert v2.ELIGIBLE_CATEGORIES == ("incorrect_solution", "invalid_candidate")


def test_calibration_keeps_only_1_to_6_successes_out_of_8_judged() -> None:
    assert [v2.calibration_keeps(s, 8) for s in range(9)] == [False, *([True] * 6), False, False]
    assert not v2.calibration_keeps(3, 7)  # 8 tirages JUGÉS exigés


def test_latin_square_rotates_and_balances_positions() -> None:
    orders = [v2.arm_order(i) for i in range(6)]
    assert orders[:3] == [("A", "B", "C"), ("B", "C", "A"), ("C", "A", "B")] and orders[3:] == orders[:3]
    for position in range(3):
        assert sorted(o[position] for o in orders[:3]) == ["A", "B", "C"]


def test_task_order_and_stop_rule_depend_only_on_run1() -> None:
    assert [v2.task_for(i, ["x", "y", "z"]) for i in range(5)] == ["x", "y", "z", "x", "y"]
    assert not v2.should_stop(59, 239) and v2.should_stop(60, 10) and v2.should_stop(3, 240)
    import inspect

    assert list(inspect.signature(v2.should_stop).parameters) == ["n_eligible", "n_run1"]


def test_mcnemar_exact_two_sided() -> None:
    assert v2.mcnemar_exact(0, 0) is None
    assert v2.mcnemar_exact(6, 0) == pytest.approx(2 / 64)
    assert v2.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert v2.mcnemar_exact(5, 5) == 1.0
    assert v2.mcnemar_exact(15, 5) == pytest.approx(0.04138947, rel=1e-6)


def test_holm_adjustment() -> None:
    assert v2.holm({"C_vs_A": 0.01, "B_vs_A": 0.04}) == {"C_vs_A": 0.02, "B_vs_A": 0.04}
    assert v2.holm({"C_vs_A": 0.03, "B_vs_A": 0.02}) == {"C_vs_A": 0.04, "B_vs_A": 0.04}
    assert v2.holm({"C_vs_A": 0.02, "B_vs_A": None}) == {"C_vs_A": 0.04, "B_vs_A": None}


def test_decision_rules() -> None:
    ok = {"infra_share": 0.0, "n_eligible": 60, "capped": False}
    assert v2.decide(18, 4, 0.01, **ok) == "amélioration mesurée"
    assert v2.decide(4, 18, 0.01, **ok) == "régression mesurée"
    assert v2.decide(11, 10, 0.9, **ok) == "aucune différence démontrée"
    assert v2.decide(6, 4, 0.7, **ok) == "protocole insuffisant pour conclure"  # < 20 discordantes
    assert (
        v2.decide(11, 10, 0.9, infra_share=0.2, n_eligible=60, capped=False)
        == "protocole insuffisant pour conclure"
    )
    assert (
        v2.decide(11, 10, 0.9, infra_share=0.0, n_eligible=40, capped=True)
        == "protocole insuffisant pour conclure"
    )


def test_machine_parsers_never_invent_a_value() -> None:
    gpus = v2.parse_nvidia_smi("NVIDIA GeForce RTX 4060 Ti, 560.94, 5321, 8188\n")
    assert gpus == [
        {
            "name": "NVIDIA GeForce RTX 4060 Ti",
            "driver": "560.94",
            "vram_used_mib": 5321,
            "vram_total_mib": 8188,
        }
    ]
    assert v2.parse_nvidia_smi("") is None and v2.parse_nvidia_smi("garbage") is None
    models = v2.parse_ollama_models({"models": [{"name": "m", "size": 1000, "size_vram": 250}]})
    assert models == [
        {"name": "m", "size_bytes": 1000, "size_vram_bytes": 250, "vram_share": 0.25, "ram_share": 0.75}
    ]
    assert v2.parse_ollama_models({"models": [{"name": "m"}]})[0]["vram_share"] is None
    assert v2.parse_ollama_models("x") is None


# -- contrôles de validité (injection de défauts) ---------------------------------------------------------

_FACTS = {
    "failure_category": "incorrect_solution",
    "failed_cases": 3,
    "total_cases": 12,
    "exception_class_count": 0,
    "exception_classes": [],
    "script_terminated": True,
    "exit_code": 0,
}
_RUN1 = {"run_id": "r1", "experience_id": "e1", "eval_run_id": "ev1", "failure_facts": _FACTS}


def _c_row(**overrides):
    from apex.metalearner.failure_facts import PROMPT_VERSION, FailureFacts, render_failure_facts

    text = render_failure_facts(FailureFacts(**{**_FACTS, "exception_classes": ()}))
    row = {
        "condition_on_run": "r1",
        "source_experience_id": "e1",
        "strategy": "failure_facts",
        "base_temperature": 0.7,
        "experience_id": "e2",
        "prompt_version": PROMPT_VERSION,
        "source_eval_run_id": "ev1",
        "failure_facts": dict(_FACTS),
        "generation": {"prompt_suffix": text, "temperature": 0.7},
    }
    row.update(overrides)
    return row


def _violations(arm, row, hidden=()):
    return v2.arm_violations(arm, row, _RUN1, hidden=list(hidden), expected_answer="[true, false]")


def test_a_conforming_arm_c_passes_the_checks() -> None:
    assert _violations("C", _c_row()) == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"base_temperature": 0.9},
        {"generation": {"prompt_suffix": "x", "temperature": 1.1}},
        {"strategy": "rule"},
        {"condition_on_run": "autre"},
        {"source_eval_run_id": "autre"},
        {"prompt_version": "v0"},
        {"failure_facts": {**_FACTS, "message": "KeyError('nurses run')"}},
        {"failure_facts": {**_FACTS, "failed_cases": 1}},
    ],
)
def test_any_deviation_of_arm_c_is_a_violation(mutation) -> None:
    assert _violations("C", _c_row(**mutation))


def test_a_hidden_input_or_expected_answer_in_the_text_is_a_leak() -> None:
    row = _c_row()
    row["generation"] = {
        **row["generation"],
        "prompt_suffix": row["generation"]["prompt_suffix"] + " nurses run",
    }
    assert any("FUITE" in v for v in _violations("C", row, hidden=["nurses run"]))
    row["generation"]["prompt_suffix"] = "[true, false]"
    assert any("FUITE" in v for v in _violations("C", row))


def test_arm_a_and_run1_must_be_bare_at_t0_and_b_must_be_the_rule() -> None:
    bare = {
        "condition_on_run": None,
        "base_temperature": 0.7,
        "experience_id": "e",
        "generation": {"temperature": 0.7},
    }
    assert _violations("A", bare) == [] and _violations("RUN1", bare) == []
    assert _violations("A", {**bare, "base_temperature": None})
    assert _violations("A", {**bare, "generation": {"temperature": 0.7, "prompt_suffix": "indice"}})
    assert _violations("A", {**bare, "condition_on_run": "r1"})
    rule = {
        "condition_on_run": "r1",
        "source_experience_id": "e1",
        "strategy": "rule",
        "experience_id": "e3",
        "generation": {"temperature": 1.1, "prompt_suffix": _INCORRECT_SOLUTION_HINT},
    }
    assert _violations("B", rule) == []
    assert _violations("B", {**rule, "strategy": "failure_facts"})
    assert _violations("B", {**rule, "generation": {"temperature": 1.1, "prompt_suffix": "autre texte"}})


def test_public_text_is_not_a_leak_but_every_task_keeps_secret_inputs() -> None:
    """« Python » est une entrée cachée de count_vowels ET un mot de l'énoncé et du rappel de B : jamais une
    fausse fuite ; chaque tâche garde des entrées cachées à surveiller."""
    from apex.taskgen.solution_tasks import REFERENCE_TASK_KINDS

    hidden, _ = v2._hidden("count_vowels")
    assert "Python" not in hidden and "rhythm" in hidden
    for kind in REFERENCE_TASK_KINDS:
        hidden, expected = v2._hidden(kind)
        assert [h for h in hidden if len(h) >= 4] and expected
        for hint in v2._RULE_HINTS:
            assert not [h for h in hidden if len(h) >= 4 and h in hint], kind


# -- intégration (vraie API / boucle / sandbox / Judge, modèle doublure) ----------------------------------


class _ClientApi:
    """Même interface que `first_real_loop.Api`, sur l'application FastAPI en processus."""

    pid = None

    def __init__(self, client) -> None:
        self.client = client

    def call(self, method, path, body=None, timeout_s=60):
        response = self.client.request(method, "/api/v1" + path, json=body)
        return response.status_code, response.json()


@pytest.fixture
def api(monkeypatch):
    import functools

    from fastapi.testclient import TestClient

    from apex.agents.runtime import AgentRuntime
    from apex.api import dependencies
    from apex.api.app import create_app
    from apex.taskloop.loop import run_task_loop
    from apex.taskloop.registry import TaskLoopRegistry
    from apex.taskloop.runs import TaskRunService
    from tests.api_test_support import TEST_API_TOKEN
    from tests.test_protocol_v2 import WRONG
    from tests.test_taskloop_execution import (
        _FakeModelProvider,
        a_candidate,
        a_pool,
        sqlite_session_factory,
    )

    factory = sqlite_session_factory()
    loop = functools.partial(
        run_task_loop,
        candidates=(a_candidate(),),
        registry=TaskLoopRegistry(),
        provider_factory=lambda _c: _FakeModelProvider(mode="ok", text=WRONG),
    )
    service = TaskRunService(factory, AgentRuntime(factory), a_pool(), run_loop=loop)
    monkeypatch.setattr(dependencies, "_task_run_service", service)
    monkeypatch.setattr(dependencies, "get_session_factory", lambda: factory)
    real_sleep = time.sleep
    monkeypatch.setattr(v2.time, "sleep", lambda _s: real_sleep(0.05))
    monkeypatch.setattr(v2, "machine_facts", lambda: {"cpu": None})
    return _ClientApi(TestClient(create_app(), headers={"Authorization": f"Bearer {TEST_API_TOKEN}"}))


def test_calibration_then_experiment_then_analysis_end_to_end(api, tmp_path, monkeypatch) -> None:
    assert v2.calibrate(api, tmp_path, label="replication", kinds=["is_palindrome"], timeout_s=120) == 0
    calibration = json.loads((tmp_path / "calibration.json").read_text())
    assert calibration["tasks"]["is_palindrome"] == {
        "judged": 8,
        "successes": 0,
        "infra": 0,
        "retained": False,
    }
    assert calibration["retained"] == []  # 0/8 : écartée — et aucun RUN 2 n'a été exécuté
    rows = [json.loads(line) for line in (tmp_path / "calibration.jsonl").read_text().splitlines()]
    assert {r["arm"] for r in rows} == {"RUN1"} and len(rows) == 8
    assert v2.main_experiment(api, tmp_path, label="replication", timeout_s=120) == 3  # rien à lancer

    # TEST SEULEMENT : on force la tâche retenue pour exercer l'enchaînement (une doublure ne se calibre pas).
    calibration["retained"] = ["is_palindrome"]
    (tmp_path / "calibration.json").write_text(json.dumps(calibration))
    monkeypatch.setattr(v2, "MAX_ELIGIBLE", 3)
    assert v2.main_experiment(api, tmp_path, label="replication", timeout_s=120) == 0
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    sequence = [r["arm"] for r in rows if r["arm"] in ("RUN1", "A", "B", "C")]
    assert sequence == ["RUN1", "A", "B", "C", "RUN1", "B", "C", "A", "RUN1", "C", "A", "B"]
    assert all(not r.get("violations") for r in rows if "violations" in r)
    c_rows = [r for r in rows if r["arm"] == "C"]
    assert all(
        r["failure_facts"]["failed_cases"] == 3 and r["generation"]["temperature"] == 0.7 for r in c_rows
    )

    summary = v2.analyse(rows)
    assert summary["n_run1"] == 3 and summary["n_eligible"] == 3 and summary["n_complete"] == 3
    assert summary["invalid"] == []
    for comp in summary["comparisons"].values():
        assert comp["pairs"] == 3 and comp["discordant"] == 0 and comp["p"] is None
        assert comp["decision"] == "protocole insuffisant pour conclure"


def test_analysis_refuses_mixed_models_violations_and_unfinished_runs() -> None:
    def inst(i, arm, success, model="m"):
        return {
            "arm": arm,
            "instance": i,
            "success": success,
            "model": model,
            "label": "replication",
            "eval_run_id": "e",
            "failure_category": "success" if success else "incorrect_solution",
        }

    rows = [
        {
            "arm": "RUN1",
            "model": "m",
            "label": "replication",
            "failure_category": "incorrect_solution",
            "eval_run_id": "e",
        }
    ]
    rows += [inst(0, "A", False), inst(0, "B", True), inst(0, "C", True, model="autre")]
    summary = v2.analyse(rows)
    assert any("mélangés" in i for i in summary["invalid"]) and any(
        "inachevée" in i for i in summary["invalid"]
    )
    assert {c["decision"] for c in summary["comparisons"].values()} == {
        "protocole insuffisant pour conclure"
    }


def test_primary_run_is_refused_without_a_target_validation_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["x", "run", "--label", "primaire", "--out", str(tmp_path)])
    monkeypatch.setattr(v2, "_api", lambda: pytest.fail("aucun appel à APEX ne doit avoir lieu"))
    assert v2.main() == 2
