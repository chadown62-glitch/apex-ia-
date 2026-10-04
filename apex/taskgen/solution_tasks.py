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


# -- Tâches contrôlées de l'expérience RUN 1 -> RUN 2 (docs/EXPERIENCE_RUN1_RUN2.md) ----------------------
# Même contrat que `is_palindrome` : énoncé montré au modèle, cas cachés JAMAIS montrés, réponses attendues
# qui ne quittent jamais l'hôte. Chaque énoncé porte UNE contrainte précise, facile à manquer (constat run 23/24 :
# le modèle 1.5B a ignoré « PAS les espaces ni la ponctuation ») — figées AVANT toute mesure, jamais ajustées
# après coup pour fabriquer une amélioration. Arguments passés par `fonction(*arguments)`.

_FUNCTION_HARNESS_TEMPLATE = """\
{marker}

import json as _apex_json

_APEX_INPUTS = {inputs!r}
_apex_results = []
for _apex_args in _APEX_INPUTS:
    try:
        _apex_results.append({function}(*_apex_args))
    except Exception as _apex_exc:
        _apex_results.append({{"error": repr(_apex_exc)}})
print({prefix!r} + _apex_json.dumps({{"results": _apex_results}}, default=repr))
"""

_ANSWER_FORMAT = (
    " Réponds UNIQUEMENT avec le code Python de la fonction (aucune explication, aucun bloc markdown, "
    "aucun appel d'exemple)."
)

_CONTROLLED_TASKS: dict[str, tuple[str, str, tuple[tuple[tuple, object], ...]]] = {
    "rle_encode": (
        "rle_encode",
        "Écris une fonction Python nommée exactement `rle_encode` qui prend une chaîne `s` et retourne son "
        "encodage par plages : chaque suite de caractères identiques consécutifs devient le caractère suivi "
        "du nombre de répétitions, nombre TOUJOURS écrit, même quand il vaut 1 (« aab » donne « a2b1 »). "
        "L'encodage est sensible à la casse. Une chaîne vide donne une chaîne vide.",
        (
            (("aaab",), "a3b1"), (("abc",), "a1b1c1"), (("",), ""), (("aA",), "a1A1"),
            (("zzzzzzzzzzzz",), "z12"), (("aabbaa",), "a2b2a2"), (("x",), "x1"), (("  ",), " 2"),
        ),
    ),
    "count_vowels": (
        "count_vowels",
        "Écris une fonction Python nommée exactement `count_vowels` qui prend une chaîne `s` et retourne le "
        "nombre de voyelles a, e, i, o, u qu'elle contient, sans tenir compte de la casse. La lettre y n'est "
        "PAS une voyelle ici, et les lettres accentuées (é, è, à, ...) ne sont PAS comptées.",
        (
            (("hello",), 2), (("HELLO",), 2), (("rhythm",), 0), (("yay",), 1), (("",), 0),
            (("AEIOUaeiou",), 10), (("éléphant",), 1), (("Python",), 1),
        ),
    ),
    "word_count": (
        "word_count",
        "Écris une fonction Python nommée exactement `word_count` qui prend une chaîne `s` et retourne son "
        "nombre de mots. Un mot est une suite maximale de caractères qui ne sont pas des blancs (espace, "
        "tabulation, saut de ligne) ; la ponctuation attachée fait partie du mot, et « peut-être » compte "
        "pour UN seul mot. Une chaîne vide ou faite uniquement de blancs donne 0.",
        (
            (("hello world",), 2), (("",), 0), (("   ",), 0), (("a\tb\nc",), 3), (("peut-être demain",), 2),
            (("  lead and trail  ",), 3), (("one,two three",), 2), (("x",), 1),
        ),
    ),
    "second_largest": (
        "second_largest",
        "Écris une fonction Python nommée exactement `second_largest` qui prend une liste d'entiers `nums` "
        "et retourne la deuxième plus grande valeur DISTINCTE de la liste, ou None s'il y a moins de deux "
        "valeurs distinctes.",
        (
            (([1, 2, 3],), 2), (([5, 5, 4],), 4), (([7, 7],), None), (([],), None), (([-1, -2],), -2),
            (([3, 1, 3, 2],), 2), (([10],), None), (([2, 2, 3, 3],), 2),
        ),
    ),
}

#: Toutes les tâches de référence disponibles (`POST /tasks/reference`, protocole RUN 1 -> RUN 2).
REFERENCE_TASK_KINDS: tuple[str, ...] = ("is_palindrome", *_CONTROLLED_TASKS)


def build_reference_task_spec(kind: str) -> SolutionTaskSpec:
    if kind == "is_palindrome":
        return build_palindrome_task_spec()
    if kind not in _CONTROLLED_TASKS:
        raise KeyError(kind)
    function, description, cases = _CONTROLLED_TASKS[kind]
    harness = _FUNCTION_HARNESS_TEMPLATE.format(
        marker=CANDIDATE_CODE_MARKER, inputs=[list(args) for args, _ in cases], function=function,
        prefix=RESULT_LINE_PREFIX,
    )
    return SolutionTaskSpec(
        title=f"Solution générée — {function}",
        description=description + _ANSWER_FORMAT,
        command=PLACEHOLDER_COMMAND,
        solution_harness=harness,
        expected_answer=json.dumps([expected for _, expected in cases]),
    )
