"""DEPENDENCY ORDER à LIAISON EXPLICITE (P3.7).

Même tâche, même générateur, même entrée (instructions mélangées + question) que
`dependency_order` ; SEULE la réponse change : chaque instruction exécutée est recopiée avant sa
valeur (`a=3>3,b=a+4>7,c=b-a>4=4`). La valeur d'une étape se calcule alors à partir d'un texte déjà
écrit juste avant elle, au lieu d'une instruction située ailleurs dans l'entrée. Motivation mesurée
(P4, `docs/P3_5_CONTINUAL_AND_LIVE.md` §6.6) : à modèle, données et budget égaux, ce format fait
passer l'exactitude d'un spécialiste de 0,11 à 0,76 (graine 0) et de 0,19 à 0,58 (graine 1).

Une réponse n'est juste que si les instructions recopiées sont exactement celles du programme dans
l'ordre d'exécution, chaque valeur et la réponse exactes (coût 0) ; sinon coût 1."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from apex.capabilities.dependency_order.benchmark import OrderProblem, suite_name
from apex.capabilities.dependency_order.capability import CONTRACT as IMPLICIT
from apex.capabilities.dependency_order.capability import DependencyOrderCapability

NAME = "dependency_order_explicit"
TASKS_DOMAIN = "order_explicit_tasks"
TASKS_PREFIX = "task:dependency_order_explicit/"
MAX_NEW_TOKENS = 128
_ANSWER = re.compile(r"((?:[a-z]=[^,>]+>-?\d+,)*[a-z]=[^,>]+>-?\d+)=(-?\d+)")

CONTRACT = replace(
    IMPLICIT,
    name=NAME,
    description=(
        "Exécuter un programme dont les instructions sont données DANS LE DÉSORDRE, en recopiant "
        "chaque instruction exécutée avant sa valeur ; ordre, instructions et valeurs vérifiés contre "
        "l'exécution réelle du programme ordonné dans le sandbox."
    ),
    output="trace explicite dans l'ordre d'exécution `a=3>3,b=a+4>7,c=b-a>4=4`",
    success_criteria=(
        "instructions recopiées dans l'ordre d'exécution, valeurs et réponse exactes (coût 0)",
        "acceptation : baisse de l'erreur held-out (programmes jamais vus) démontrée sur plusieurs "
        "graines, sans régression reproduite des capacités protégées",
    ),
    training_domains=(TASKS_DOMAIN,),
    acceptance_suite=suite_name("heldout", NAME),
    suites=tuple(suite_name(s, NAME) for s in ("train", "validation", "heldout")),
    memorization_suite=suite_name("train", NAME),
    provenance="générateur procédural APEX (programmes de program_trace mélangés), graine propre",
)


def score_explicit(problem: OrderProblem, text: str) -> dict[str, Any]:
    match = _ANSWER.fullmatch(text)
    if match is None:
        return {"exact": False, "well_formed": False, "steps_ok": 0, "final_ok": False, "order_ok": False}
    items = match.group(1).split(",")
    copied = [item.rsplit(">", 1)[0] for item in items]
    values = [int(item.rsplit(">", 1)[1]) for item in items]
    want = list(problem.base.values)
    program = problem.base.program.split(";")
    names = [c.partition("=")[0] for c in copied]
    return {
        "exact": copied == program and values == want and int(match.group(2)) == want[-1],
        "well_formed": True,
        "steps_ok": sum(
            1
            for got, exp in zip(
                zip(copied, values, strict=True), zip(program, want, strict=True), strict=False
            )
            if got == exp
        ),
        "final_ok": int(match.group(2)) == want[-1],
        "order_ok": names == list(problem.base.names),
        "copied_ok": copied == program,
        "textual_order": names == [s.partition("=")[0] for s in problem.statements],
    }


class ExplicitDependencyOrderCapability(DependencyOrderCapability):
    contract = CONTRACT
    tasks_prefix = TASKS_PREFIX
    max_new_tokens = MAX_NEW_TOKENS
    explicit = True

    def score(self, problem: OrderProblem, text: str) -> dict[str, Any]:
        return score_explicit(problem, text)


__all__ = ["CONTRACT", "NAME", "ExplicitDependencyOrderCapability", "score_explicit"]
