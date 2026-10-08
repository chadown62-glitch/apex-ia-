"""Mémoire ACTIONNABLE et expérience EXPLOITABLE (docs/VALIDATION_REELLE.md §6.3).

- un souvenir d'exécution est rendu dans un ordre utile (résultat, leçon, code, début de l'énoncé) au lieu d'une
  fenêtre centrée sur l'énoncé ; la leçon d'une TaskRun (diagnostic -> stratégie qui a marché) y est écrite ;
- « mémoire utilisée » est MESURÉE (éléments distinctifs du souvenir retrouvés dans le code), par tentative, et
  la provenance est gardée dans l'Expérience ;
- une Expérience répond aux 12 questions d'une décision future, ou dit ce qui lui manque.

Vraie base, vraie récupération, vraie boucle, vraie sandbox, vrai Judge ; modèles DOUBLURES. Prouve le
MÉCANISME, jamais un gain sur un vrai modèle (qui se MESURE, §6.3).
"""

from __future__ import annotations

import json

from apex.experience.lesson import FIELDS, experience_card, next_strategy, run_lesson
from apex.memory.lessons import execution_view, use_evidence
from apex.memory.retrieval import snippet
from apex.memory.service import MemoryService
from apex.metalearner.experience import ExperienceRepository
from tests.test_memory_system import _execution_records, _Recorder, _start, _task, factory  # noqa: F401
from tests.test_protocol_v2 import WRONG
from tests.test_taskloop_adaptation import _kinds, _run, _Scripted  # noqa: F401
from tests.test_taskloop_execution import _CORRECT_IS_PALINDROME

TARIFS = ("Écris `zephyr_cost(weight, zone)` : tarifs Zéphyr, zone A : 4.90 + 1.20 par kilo, zone B : 7.50 + 2.35 "
          "par kilo ; zone inconnue : None.")
ZEPHYR = ("TARIFS = {'A': (4.9, 1.2), 'B': (7.5, 2.35)}\n\n\ndef zephyr_cost(weight, zone):\n"
          "    if zone not in TARIFS:\n        return None\n    base, per_kg = TARIFS[zone]\n"
          "    return round(base + per_kg * weight, 2)\n")
ORDER = ("Écris `zephyr_order(parcels)` : coût total d'une liste de colis (poids, zone) au tarif Zéphyr de la tâche "
         "déjà traitée ; colis invalide ignoré.")
ORDER_USING = ("def zephyr_order(parcels):\n    tarifs = {'A': (4.9, 1.2), 'B': (7.5, 2.35)}\n    total = 0\n"
               "    for w, z in parcels:\n        if z in tarifs:\n            total += tarifs[z][0] + tarifs[z][1] * w\n"
               "    return round(total, 2)\n")
ORDER_GUESSING = ("def zephyr_order(parcels):\n    total = 0\n    for w, z in parcels:\n"
                  "        total += 5 + w\n    return round(total, 2)\n")
DIAGNOSIS = "Le code retire espaces et ponctuation alors que l'énoncé impose de les garder."


def _scripted_run(factory, codes, **kwargs):  # noqa: F811
    _Scripted.requests = []  # le script est indexé par le nombre d'appels : jamais hérité d'un autre test
    return _run(factory, codes, **kwargs)


def test_the_execution_view_puts_result_and_code_before_the_statement() -> None:
    content = ("Tâche : Zéphyr\nÉnoncé : " + "tarifs Zéphyr " * 150 + "\nRésultat : completed après 1 tentative(s) "
               "jugée(s), dernier verdict pass\nLeçon : la 1re tentative a échoué (incorrect_solution) ; cause "
               "identifiée : arrondi oublié ; corrigée par la stratégie « repair ».\n"
               "Solution validée par le Judge (code produit) :\n" + ZEPHYR)
    view = execution_view(content)
    assert view.index("Résultat :") < view.index("Leçon :") < view.index("TARIFS = ") < view.index("Énoncé de cette")
    assert "2.35" in view and len(view) < 1_200  # énoncé borné, code entier
    # La fenêtre d'avant (600 caractères centrés sur l'énoncé) ne montrait NI le code NI la leçon.
    old = snippet(content, "coût total tarifs Zéphyr colis", 600)
    assert "TARIFS = " not in old and "Leçon" not in old


def test_use_evidence_counts_only_distinctive_elements_absent_from_the_statement() -> None:
    shown = execution_view(f"Tâche : Zéphyr\nÉnoncé : {TARIFS}\nRésultat : pass\nSolution :\n{ZEPHYR}")
    evidence = use_evidence(shown, ORDER, ORDER_USING)
    assert "nombre 2.35" in evidence and "nombre 7.5" in evidence
    assert use_evidence(shown, ORDER, ORDER_GUESSING) == []  # 5, 0 : trop banals pour prouver quoi que ce soit
    assert use_evidence(shown, ORDER + " Tarif B : 2.35 par kilo.", "x = 2.35") == []  # déjà dans l'énoncé
    assert use_evidence("", ORDER, ORDER_USING) == [] and use_evidence(shown, ORDER, "") == []


def _zephyr_source(factory):  # noqa: F811
    task = _task(factory, function="zephyr_cost", description=TARIFS,
                 cases=[{"args": [1, "A"], "expected": 6.1}, {"args": [2, "B"], "expected": 12.2}])
    return _start(factory, task, ZEPHYR)


def test_memory_use_is_measured_per_attempt_and_kept_as_decision_provenance(factory) -> None:  # noqa: F811
    source = _zephyr_source(factory)
    assert source.status.value == "completed"
    _Recorder.prompts = []
    target = _task(factory, function="zephyr_order", description=ORDER,
                   cases=[{"args": [[[1, "A"], [2, "B"]]], "expected": 18.3}])
    run = _start(factory, target, ORDER_USING, use_memory=True)
    (prompt,) = _Recorder.prompts
    assert "TARIFS = {'A': (4.9, 1.2)" in prompt and "Résultat : completed" in prompt
    (retrieved,) = [e for e in _kinds(factory, run.id) if e["kind"] == "memory.retrieved"]
    assert retrieved["items"][0]["view"] == "execution" and retrieved["items"][0]["source"] == f"task_run:{source.id}"
    (used,) = [e for e in _kinds(factory, run.id) if e["kind"] == "memory.used"]
    assert used["items"][0]["used"] is True and "nombre 2.35" in used["items"][0]["evidence"]
    (experience,) = [e for e in ExperienceRepository(factory).list_by_task(target.id) if e.experiment_ref == run.id]
    assert experience.generation_config["memory_used"] == [retrieved["items"][0]["id"]]
    assert run.status.value == "completed"


def test_memory_shown_but_not_used_is_reported_as_such(factory) -> None:  # noqa: F811
    _zephyr_source(factory)
    target = _task(factory, function="zephyr_order", description=ORDER,
                   cases=[{"args": [[[1, "A"]]], "expected": 6.1}])
    run = _start(factory, target, ORDER_GUESSING, use_memory=True)
    (used,) = [e for e in _kinds(factory, run.id) if e["kind"] == "memory.used"]
    assert [i["used"] for i in used["items"]] == [False]
    (experience,) = [e for e in ExperienceRepository(factory).list_by_task(target.id) if e.experiment_ref == run.id]
    assert experience.generation_config["memory_used"] == [] and run.status.value == "failed"


def test_a_repaired_run_leaves_a_lesson_in_memory_and_never_a_hidden_value(factory) -> None:  # noqa: F811
    task, run = _scripted_run(factory, [WRONG, DIAGNOSIS, _CORRECT_IS_PALINDROME], retry_adaptation="diagnose_repair")
    assert run.status.value == "completed"
    (record,) = [r for r in MemoryService(factory)._repo.list_system_records(categories=["execution"], limit=10)
                 if r.source == f"task_run:{run.id}"]
    assert (f"Leçon : la 1re tentative a échoué (incorrect_solution) ; cause identifiée : {DIAGNOSIS} ; corrigée "
            "par la stratégie « repair »") in record.content
    for value in json.loads(task.expected_answer):
        assert not (isinstance(value, str) and len(value) > 3 and repr(value) in record.content)
    assert "Leçon :" in execution_view(record.content)


def test_a_run_without_diagnosis_has_no_lesson(factory) -> None:  # noqa: F811
    _, run = _scripted_run(factory, [WRONG, _CORRECT_IS_PALINDROME], retry_adaptation="failure_facts")
    (record,) = [r for r in MemoryService(factory)._repo.list_system_records(categories=["execution"], limit=10)
                 if r.source == f"task_run:{run.id}"]
    assert "Leçon" not in record.content


def test_an_experience_answers_the_twelve_questions_or_says_what_is_missing(factory) -> None:  # noqa: F811
    task, run = _scripted_run(factory, [WRONG, DIAGNOSIS, _CORRECT_IS_PALINDROME], retry_adaptation="diagnose_repair")
    first, second = sorted((e for e in ExperienceRepository(factory).list_by_task(task.id) if e.experiment_ref == run.id),
                           key=lambda e: (e.created_at, e.id))
    card = experience_card(second, task_title=task.title)
    assert set(FIELDS) <= set(card) and card["missing"] == ["tools"]  # aucun épisode outillé : dit, pas inventé
    assert card["likely_cause"] == DIAGNOSIS and card["strategy"]["mode"] == "repair"
    assert card["observation"]["failed_cases"] == 3 and card["result"] == "pass" and card["score"] == second.fitness > first.fitness
    assert card["next_strategy"] == "réutiliser « repair »"
    initial = experience_card(first)
    assert initial["error"] == "incorrect_solution" and initial["next_strategy"].startswith("diagnostiquer")
    assert {"observation", "likely_cause", "modification", "tools"} <= set(initial["missing"])
    assert run_lesson([first]) is None and "corrigée" in run_lesson([first, second])


def test_next_strategy_rules() -> None:
    from types import SimpleNamespace

    from apex.experience.failure_taxonomy import FailureCategory

    def exp(category, config=None, delta=None):
        return SimpleNamespace(failure_category=FailureCategory(category), generation_config=config or {},
                               improvement_delta=delta)

    assert next_strategy(exp("infrastructure_failure")).startswith("réessayer à l'identique")
    assert next_strategy(exp("incorrect_solution", {"repair": {"mode": "repair"}}, 0.0)).startswith("réécrire")
    assert next_strategy(exp("incorrect_solution", {"repair": {"mode": "repair"}}, 0.25)).startswith("diagnostiquer")
