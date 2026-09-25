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
les ENTRÉES des cas cachés, dont il publie les sorties réelles selon le
contrat de `apex.oracles.result_contract`), et `expected_answer` (les
sorties attendues, liste JSON, qui ne quittent jamais l'hôte : le verdict
est calculé hors sandbox, le candidat ne peut ni les lire ni décider de son
propre verdict par un code de sortie)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from apex.evalrunner.generated_solution import CANDIDATE_CODE_MARKER, PLACEHOLDER_COMMAND
from apex.oracles.result_contract import RESULT_LINE_PREFIX

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
    # Discriminent des algorithmes faux qui passaient les six cas ci-dessus
    # (constat du juge externe) : comparaison des seules extrémités,
    # suppression des espaces/ponctuation (interdite par l'énoncé).
    ("abca", False),
    ("abcXba", False),
    ("ab a", False),
    ("nurses run", False),
    ("No lemon, no melon", False),
    ("aB ba", True),
)

_HARNESS_TEMPLATE = """\
{marker}

import json as _apex_json

_APEX_INPUTS = {inputs!r}
_apex_results = []
for _apex_input in _APEX_INPUTS:
    try:
        _apex_results.append(is_palindrome(_apex_input))
    except Exception as _apex_exc:
        _apex_results.append({{"error": repr(_apex_exc)}})
print({prefix!r} + _apex_json.dumps({{"results": _apex_results}}, default=repr))
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
    expected_answer: str


def build_palindrome_task_spec() -> SolutionTaskSpec:
    inputs = [s for s, _ in _HIDDEN_TEST_CASES]
    harness = _HARNESS_TEMPLATE.format(
        marker=CANDIDATE_CODE_MARKER, inputs=inputs, prefix=RESULT_LINE_PREFIX
    )
    return SolutionTaskSpec(
        title="Solution générée — is_palindrome",
        description=_DESCRIPTION,
        command=PLACEHOLDER_COMMAND,
        solution_harness=harness,
        expected_answer=json.dumps([expected for _, expected in _HIDDEN_TEST_CASES]),
    )
