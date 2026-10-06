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

import builtins
import json
import keyword
import math
import re
from dataclasses import dataclass
from typing import Any

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
    #: `function` (défaut) ou `project` (voir `apex.tasks.model.TaskKind`) ; fichiers visibles de départ.
    kind: str = "function"
    workspace_seed: dict[str, str] | None = None


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
    # -- Protocole v2 (docs/PROTOCOLE_V2.md) : 10 tâches ajoutées le 05/10/2026, AVANT toute calibration et
    # toute mesure v2. Même règle : une contrainte précise par énoncé ; l'exemple de l'énoncé n'est jamais un
    # cas caché. L'éligibilité (1 à 6 succès sur 8 tirages du RUN 1) est décidée par la calibration seule.
    "roman_to_int": (
        "roman_to_int",
        "Écris une fonction Python nommée exactement `roman_to_int` qui prend une chaîne `s` écrite en chiffres "
        "romains MAJUSCULES (I, V, X, L, C, D, M ; notation soustractive comme « XC » = 90) et retourne sa "
        "valeur entière. Si la chaîne est vide ou contient le moindre autre caractère (minuscules comprises), "
        "retourne None.",
        (
            (("III",), 3), (("IV",), 4), (("IX",), 9), (("LVIII",), 58), (("MCMXCIV",), 1994),
            (("iv",), None), (("",), None), (("XLII",), 42),
        ),
    ),
    "merge_intervals": (
        "merge_intervals",
        "Écris une fonction Python nommée exactement `merge_intervals` qui prend une liste d'intervalles "
        "fermés `[début, fin]` (entiers, début <= fin), dans un ordre quelconque, et retourne la liste des "
        "intervalles fusionnés, triée par début, chaque intervalle sous forme de liste `[début, fin]`. Deux "
        "intervalles qui se TOUCHENT seulement par une borne (comme [1, 2] et [2, 7]) sont aussi fusionnés.",
        (
            (([[1, 3], [2, 6], [8, 10]],), [[1, 6], [8, 10]]), (([[1, 4], [4, 5]],), [[1, 5]]), (([],), []),
            (([[5, 6], [1, 2]],), [[1, 2], [5, 6]]), (([[1, 10], [2, 3]],), [[1, 10]]),
            (([[1, 2], [3, 4]],), [[1, 2], [3, 4]]), (([[0, 0]],), [[0, 0]]),
            (([[3, 5], [1, 3], [6, 7]],), [[1, 5], [6, 7]]),
        ),
    ),
    "luhn_valid": (
        "luhn_valid",
        "Écris une fonction Python nommée exactement `luhn_valid` qui prend une chaîne `s` et retourne True si "
        "c'est un numéro valide selon l'algorithme de Luhn, False sinon. Les espaces sont ignorés ; tout autre "
        "caractère non chiffre rend le numéro invalide ; un numéro de MOINS DE DEUX chiffres (après retrait "
        "des espaces) est invalide.",
        (
            (("4539 3195 0343 6467",), True), (("8273 1232 7352 0569",), False), (("0",), False),
            (("059",), True), (("59",), True), (("055 444 285",), True), (("055a 444 285",), False),
            (("  ",), False),
        ),
    ),
    "balanced": (
        "balanced",
        "Écris une fonction Python nommée exactement `balanced` qui prend une chaîne `s` et retourne True si "
        "ses parenthèses (), crochets [] et accolades {} sont correctement appariés et imbriqués, False sinon. "
        "Tous les AUTRES caractères sont ignorés. Une chaîne vide est équilibrée.",
        (
            (("([]{})",), True), (("([)]",), False), (("",), True), (("((",), False), (("a(b)c",), True),
            (("}{",), False), (("{[()()]}",), True), (("(]",), False),
        ),
    ),
    "camel_to_snake": (
        "camel_to_snake",
        "Écris une fonction Python nommée exactement `camel_to_snake` qui convertit un identifiant camelCase "
        "ou PascalCase `s` en snake_case minuscule. Une suite de majuscules (acronyme) forme UN seul mot : "
        "« getHTTPCode » donne « get_http_code ». Un chiffre reste collé au mot qui le précède. Un "
        "identifiant déjà en snake_case est retourné inchangé.",
        (
            (("parseHTTPResponse",), "parse_http_response"), (("HTTPServer",), "http_server"),
            (("simple",), "simple"), (("getX",), "get_x"), (("UserID",), "user_id"),
            (("already_snake",), "already_snake"), (("XMLHttpRequest",), "xml_http_request"),
            (("version2Update",), "version2_update"),
        ),
    ),
    "median": (
        "median",
        "Écris une fonction Python nommée exactement `median` qui prend une liste de nombres `nums` (NON "
        "triée) et retourne sa médiane : la valeur du milieu après tri, ou, pour un nombre pair d'éléments, la "
        "MOYENNE des deux valeurs du milieu. Une liste vide donne None.",
        (
            (([3, 1, 2],), 2), (([4, 1, 3, 2],), 2.5), (([],), None), (([5],), 5), (([1, 1, 1, 1],), 1),
            (([10, -1],), 4.5), (([7, 3, 5, 1, 9],), 5), (([2, 8],), 5),
        ),
    ),
    "chunk": (
        "chunk",
        "Écris une fonction Python nommée exactement `chunk` qui prend une liste `lst` et un entier `n` et "
        "retourne la liste des morceaux consécutifs de `n` éléments (le dernier peut être plus court), chaque "
        "morceau étant une liste. Si `n` est inférieur ou égal à 0, retourne une liste vide.",
        (
            (([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]), (([], 3), []), (([1, 2], 5), [[1, 2]]),
            (([1, 2, 3], 0), []), (([1, 2, 3], -1), []), (([1, 2, 3, 4], 2), [[1, 2], [3, 4]]),
            (([1], 1), [[1]]), (([1, 2, 3], 3), [[1, 2, 3]]),
        ),
    ),
    "parse_duration": (
        "parse_duration",
        "Écris une fonction Python nommée exactement `parse_duration` qui prend une chaîne `s` de la forme "
        "« <h>h<m>m<s>s » et retourne la durée totale en secondes (« 2h3s » donne 7203). Chaque partie est "
        "facultative mais les parties présentes apparaissent DANS CET ORDRE (h, puis m, puis s), sans espace, "
        "avec au moins une partie. Toute autre chaîne (vide, nombre sans unité, ordre différent) donne None.",
        (
            (("1h",), 3600), (("30m",), 1800), (("1h30m",), 5400), (("45s",), 45), (("2h5s",), 7205),
            (("",), None), (("10",), None), (("1m1h",), None),
        ),
    ),
    "title_case": (
        "title_case",
        "Écris une fonction Python nommée exactement `title_case` qui prend une chaîne `s` de mots séparés par "
        "un espace et retourne la chaîne où chaque mot a sa première lettre en majuscule et les suivantes en "
        "minuscules, SAUF les petits mots de, la, le, et, du, des, entièrement en minuscules — sauf s'ils sont "
        "le premier mot. Une chaîne vide donne une chaîne vide.",
        (
            (("le petit prince",), "Le Petit Prince"), (("la guerre et la paix",), "La Guerre et la Paix"),
            (("HISTOIRE DE FRANCE",), "Histoire de France"), (("",), ""), (("du",), "Du"),
            (("voyage au bout de la nuit",), "Voyage Au Bout de la Nuit"),
            (("des souris et des hommes",), "Des Souris et des Hommes"), (("a",), "A"),
        ),
    ),
    "dedupe_keep_last": (
        "dedupe_keep_last",
        "Écris une fonction Python nommée exactement `dedupe_keep_last` qui prend une liste `items` et retourne "
        "une liste sans doublons où chaque valeur n'apparaît qu'une fois, à la position de sa DERNIÈRE "
        "occurrence dans la liste d'origine (l'ordre relatif de ces dernières occurrences est conservé).",
        (
            (([1, 2, 1, 3],), [2, 1, 3]), (([],), []), (([1, 1, 1],), [1]), (([3, 2, 1],), [3, 2, 1]),
            (([1, 2, 3, 2, 1],), [3, 2, 1]), ((["a", "b", "a"],), ["b", "a"]), (([5],), [5]),
            (([2, 2, 3, 3, 2],), [3, 2]),
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
    return _function_spec(function, description + _ANSWER_FORMAT, cases, title=f"Solution générée — {function}")


def _function_spec(function: str, description: str, cases: Any, *, title: str) -> SolutionTaskSpec:
    harness = _FUNCTION_HARNESS_TEMPLATE.format(
        marker=CANDIDATE_CODE_MARKER, inputs=[list(args) for args, _ in cases], function=function,
        prefix=RESULT_LINE_PREFIX,
    )
    return SolutionTaskSpec(
        title=title,
        description=description,
        command=PLACEHOLDER_COMMAND,
        solution_harness=harness,
        expected_answer=json.dumps([expected for _, expected in cases]),
    )


# -- Tâches définies par l'utilisateur -------------------------------------------------------------------
# L'utilisateur fournit un ÉNONCÉ, un NOM de fonction et des CAS (arguments, valeur attendue). APEX écrit
# lui-même le harnais (même gabarit que les tâches de référence) : l'utilisateur ne fournit JAMAIS de code
# exécuté dans la sandbox, les arguments n'y entrent que comme littéraux Python, et les valeurs attendues
# restent sur l'hôte (comparées hors sandbox par `ResultContractOracle`).

MAX_USER_CASES = 50
MAX_USER_ARGS = 10
MAX_USER_DESCRIPTION_CHARS = 4000
MAX_USER_CASES_JSON_CHARS = 100_000
_FUNCTION_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


class InvalidUserTask(ValueError):
    """Tâche utilisateur refusée — le message dit pourquoi, rien n'est créé."""


def _check_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidUserTask(f"{path} : nombre non fini refusé")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_value(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidUserTask(f"{path} : clé non textuelle refusée")
            _check_json_value(item, f"{path}.{key}")
        return
    raise InvalidUserTask(f"{path} : type {type(value).__name__} refusé (JSON seulement)")


def build_user_function_task_spec(
    *, function: str, description: str, cases: list[dict[str, Any]], title: str | None = None
) -> SolutionTaskSpec:
    """Valide puis construit une tâche « écrire la fonction `function` » à partir de cas cachés fournis par
    l'utilisateur : `cases = [{"args": [...], "expected": ...}, ...]` (valeurs JSON seulement)."""
    if not _FUNCTION_NAME.match(function) or keyword.iskeyword(function) or function.startswith("_apex"):
        raise InvalidUserTask(
            f"nom de fonction « {function} » refusé (identifiant Python en minuscules, 64 caractères au plus)"
        )
    if function.startswith("__") or function in dir(builtins):
        raise InvalidUserTask(f"nom de fonction « {function} » refusé (réservé par Python)")
    text = description.strip()
    if not text or len(text) > MAX_USER_DESCRIPTION_CHARS:
        raise InvalidUserTask(f"énoncé vide ou trop long ({MAX_USER_DESCRIPTION_CHARS} caractères au plus)")
    if not 1 <= len(cases) <= MAX_USER_CASES:
        raise InvalidUserTask(f"entre 1 et {MAX_USER_CASES} cas exigés ({len(cases)} reçus)")
    parsed: list[tuple[tuple, Any]] = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != {"args", "expected"}:
            raise InvalidUserTask(f"cas {i} : exactement « args » et « expected » attendus")
        args = case["args"]
        if not isinstance(args, list) or len(args) > MAX_USER_ARGS:
            raise InvalidUserTask(f"cas {i} : « args » doit être une liste de {MAX_USER_ARGS} arguments au plus")
        _check_json_value(args, f"cas {i}.args")
        _check_json_value(case["expected"], f"cas {i}.expected")
        parsed.append((tuple(args), case["expected"]))
    if len(json.dumps(cases)) > MAX_USER_CASES_JSON_CHARS:
        raise InvalidUserTask(f"cas trop volumineux ({MAX_USER_CASES_JSON_CHARS} caractères JSON au plus)")
    if len({json.dumps(list(args)) for args, _ in parsed}) != len(parsed):
        raise InvalidUserTask("deux cas portent les mêmes arguments")
    statement = (
        f"{text}\n\nÉcris une fonction Python nommée exactement `{function}`. Elle sera appelée avec "
        f"{_arity(parsed)} argument(s) positionnel(s) et doit RETOURNER le résultat (pas l'afficher)."
        + _ANSWER_FORMAT
    )
    return _function_spec(function, statement, parsed, title=title or f"Tâche utilisateur — {function}")


#: Débuts des consignes GÉNÉRIQUES qu'APEX ajoute après l'énoncé d'une tâche utilisateur.
_GENERATED_INSTRUCTIONS = ("\n\nÉcris une fonction Python nommée exactement `", "\n\nLe projet se trouve dans ton espace")
_NAMED_FUNCTION = re.compile(r"nommée exactement `([A-Za-z_][A-Za-z0-9_]*)`")


def retrieval_statement(description: str) -> str:
    """Ce qui DISTINGUE une tâche d'une autre, pour la récupération en mémoire : l'énoncé sans les consignes
    génériques ajoutées par APEX (contrat d'appel, format de réponse — identiques d'une tâche à l'autre, elles
    rendraient « pertinente » n'importe quelle exécution passée), plus le nom de la fonction demandée."""
    match = _NAMED_FUNCTION.search(description)
    cuts = [i for marker in _GENERATED_INSTRUCTIONS if (i := description.find(marker)) > 0]
    text = description[: min(cuts)] if cuts else description
    text = text.replace(_ANSWER_FORMAT.strip(), "").strip()
    if match and match.group(1) not in text:
        text = f"{text}\n{match.group(1)}"
    return text


def _arity(parsed: list[tuple[tuple, Any]]) -> str:
    counts = sorted({len(args) for args, _ in parsed})
    return str(counts[0]) if len(counts) == 1 else " ou ".join(str(c) for c in counts)


# -- Tâches « projet » (plusieurs fichiers) -----------------------------------------------------------------
# Une codebase de départ VISIBLE (déposée dans l'espace de travail) et des vérifications CACHÉES
# `module:fonction(args)` : le harnais, écrit par APEX, importe les modules du projet et publie les valeurs
# obtenues ; les valeurs attendues restent sur l'hôte (même contrat que les tâches « fonction » : un candidat ne
# peut pas fabriquer un succès sans les connaître). Le projet peut contenir ses propres tests (visibles).

MAX_SEED_FILES = 500
MAX_SEED_FILE_CHARS = 500_000
MAX_SEED_TOTAL_CHARS = 2_000_000
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

_PROJECT_HARNESS_TEMPLATE = """\
import importlib as _apex_importlib
import json as _apex_json
import os as _apex_os
import sys as _apex_sys

# Lancé en mode isolé (`python3 -I`) : la bibliothèque standard est résolue AVANT le projet ; le dossier du
# projet n'est ajouté qu'ensuite, EN DERNIER — un `json.py` du projet ne peut pas remplacer celui du harnais.
_apex_sys.path.append(_apex_os.getcwd())

_APEX_CASES = {cases!r}
_apex_results = []
for _apex_module, _apex_function, _apex_args in _APEX_CASES:
    try:
        _apex_target = getattr(_apex_importlib.import_module(_apex_module), _apex_function)
        _apex_results.append(_apex_target(*_apex_args))
    except Exception as _apex_exc:
        _apex_results.append({{"error": repr(_apex_exc)}})
print({prefix!r} + _apex_json.dumps({{"results": _apex_results}}, default=repr))
"""


def build_user_project_task_spec(
    *, description: str, seed_files: dict[str, str], cases: list[dict[str, Any]], title: str | None = None
) -> SolutionTaskSpec:
    """`cases = [{"module": "pkg.calc", "function": "add", "args": [...], "expected": ...}, ...]`."""
    from apex.sandbox.model import check_relative_path

    text = description.strip()
    if not text or len(text) > MAX_USER_DESCRIPTION_CHARS:
        raise InvalidUserTask(f"énoncé vide ou trop long ({MAX_USER_DESCRIPTION_CHARS} caractères au plus)")
    if not isinstance(seed_files, dict) or len(seed_files) > MAX_SEED_FILES:
        raise InvalidUserTask(f"au plus {MAX_SEED_FILES} fichiers de départ")
    total = 0
    for path, content in seed_files.items():
        try:
            check_relative_path(path)
        except ValueError as exc:
            raise InvalidUserTask(f"fichier de départ refusé : {exc}") from exc
        if path.split("/")[0].startswith(".apex"):
            raise InvalidUserTask(f"chemin réservé : {path}")
        if not isinstance(content, str) or len(content) > MAX_SEED_FILE_CHARS:
            raise InvalidUserTask(f"fichier de départ {path} : texte de {MAX_SEED_FILE_CHARS} caractères au plus")
        total += len(content)
    if total > MAX_SEED_TOTAL_CHARS:
        raise InvalidUserTask(f"projet de départ trop volumineux ({MAX_SEED_TOTAL_CHARS} caractères au plus)")
    if not 1 <= len(cases) <= MAX_USER_CASES:
        raise InvalidUserTask(f"entre 1 et {MAX_USER_CASES} vérifications exigées ({len(cases)} reçues)")
    parsed: list[tuple[str, str, list[Any]]] = []
    expected: list[Any] = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != {"module", "function", "args", "expected"}:
            raise InvalidUserTask(f"vérification {i} : exactement « module », « function », « args », « expected »")
        module, function, args = case["module"], case["function"], case["args"]
        if not isinstance(module, str) or not _MODULE.match(module) or module.startswith("_apex"):
            raise InvalidUserTask(f"vérification {i} : module « {module} » refusé")
        if not isinstance(function, str) or not _IDENT.match(function) or function.startswith("_"):
            raise InvalidUserTask(f"vérification {i} : fonction « {function} » refusée")
        if not isinstance(args, list) or len(args) > MAX_USER_ARGS:
            raise InvalidUserTask(f"vérification {i} : « args » doit être une liste de {MAX_USER_ARGS} au plus")
        _check_json_value(args, f"vérification {i}.args")
        _check_json_value(case["expected"], f"vérification {i}.expected")
        parsed.append((module, function, args))
        expected.append(case["expected"])
    if len(json.dumps(cases)) > MAX_USER_CASES_JSON_CHARS:
        raise InvalidUserTask(f"vérifications trop volumineuses ({MAX_USER_CASES_JSON_CHARS} caractères JSON au plus)")
    targets = sorted({f"`{m}.{f}`" for m, f, _ in parsed})
    statement = (
        f"{text}\n\nLe projet se trouve dans ton espace de travail. Des vérifications cachées appelleront : "
        f"{', '.join(targets)} (arguments positionnels, valeur RETOURNÉE comparée). Garde ces noms et "
        "n'exécute aucun code au chargement des modules."
    )
    harness = _PROJECT_HARNESS_TEMPLATE.format(
        cases=[[m, f, list(a)] for m, f, a in parsed], prefix=RESULT_LINE_PREFIX,
    )
    return SolutionTaskSpec(
        title=title or "Projet utilisateur", description=statement, command=PLACEHOLDER_COMMAND,
        solution_harness=harness, expected_answer=json.dumps(expected), kind="project",
        workspace_seed=dict(seed_files),
    )
