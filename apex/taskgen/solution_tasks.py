"""Première tâche RÉELLE de la passe « Generated-Solution Evaluation Loop »
(N+19) — un problème Python auto-contenu, choisi pour rester résoluble par
un modèle LOCAL modeste (`qwen2.5-coder:7b-instruct` de référence pour cette
machine, jamais un modèle 20B+/27B+/30B supposé disponible) : implémenter
`is_palindrome(s: str) -> bool`, une fonction pure, sans dépendance, sans
entrée/sortie — aucune ambiguïté de format possible.

`build_palindrome_task_spec()` retourne les champs nécessaires à
`apex.tasks.service.TaskBankService.create(...)` (jamais persisté ici — même
discipline que `apex.taskgen.model.GeneratedTaskSpec`, une proposition en
mémoire) : `description` (montrée au modèle, JAMAIS les tests), et
`solution_harness` (JAMAIS montré au modèle — porte le marqueur de
substitution `apex.evalrunner.generated_solution.CANDIDATE_CODE_MARKER` puis
des tests cachés qui font échouer le script — `sys.exit(1)` — si la fonction
générée est incorrecte ou absente)."""

from __future__ import annotations

from dataclasses import dataclass

from apex.evalrunner.generated_solution import CANDIDATE_CODE_MARKER, PLACEHOLDER_COMMAND

_DESCRIPTION = (
    "Écris une fonction Python nommée exactement `is_palindrome` qui prend un "
    "argument `s` (une chaîne de caractères) et retourne `True` si `s` est un "
    "palindrome (se lit identiquement à l'endroit et à l'envers), `False` "
    "sinon. La comparaison doit IGNORER la casse (majuscules/minuscules) mais "
    "PAS les espaces ni la ponctuation. Réponds UNIQUEMENT avec le code Python "
    "de la fonction (aucune explication, aucun bloc markdown, aucun appel "
    "d'exemple)."
)

#: Cas RÉELS, couvrant : palindrome simple, non-palindrome, insensibilité à
#: la casse, chaîne vide (cas limite réel : toujours un palindrome), chaîne
#: d'un seul caractère (cas limite réel : toujours un palindrome) — jamais
#: un cas arbitraire sans rapport avec l'énoncé.
_HIDDEN_TEST_CASES: tuple[tuple[str, bool], ...] = (
    ("racecar", True),
    ("Racecar", True),
    ("hello", False),
    ("", True),
    ("a", True),
    ("AbBa", True),
)

_HARNESS_TEMPLATE = """\
{marker}

import json, sys

_CASES = {cases!r}
_failures = []
for _s, _expected in _CASES:
    try:
        _actual = is_palindrome(_s)
    except Exception as exc:
        _failures.append({{"input": _s, "error": repr(exc)}})
        continue
    if _actual != _expected:
        _failures.append({{"input": _s, "expected": _expected, "actual": _actual}})

print(json.dumps({{"total": len(_CASES), "failures": _failures}}))
if _failures:
    print("ECHEC: " + str(len(_failures)) + " cas sur " + str(len(_CASES)) + " ont echoue", file=sys.stderr)
    sys.exit(1)
"""


@dataclass(frozen=True)
class SolutionTaskSpec:
    """Champs prêts pour `TaskBankService.create(...)` — jamais persistée
    par ce module (même discipline que `apex.taskgen.model.
    GeneratedTaskSpec`)."""

    title: str
    description: str
    command: tuple[str, ...]
    solution_harness: str


def build_palindrome_task_spec() -> SolutionTaskSpec:
    cases = tuple((s, expected) for s, expected in _HIDDEN_TEST_CASES)
    harness = _HARNESS_TEMPLATE.format(marker=CANDIDATE_CODE_MARKER, cases=list(cases))
    return SolutionTaskSpec(
        title="Solution générée — is_palindrome",
        description=_DESCRIPTION,
        command=PLACEHOLDER_COMMAND,
        solution_harness=harness,
    )
