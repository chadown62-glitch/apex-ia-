"""Tâches des bancs « cycle cognitif » (docs/VALIDATION_REELLE.md §6) — définitions PURES (aucun appel modèle).

Chaque tâche : un énoncé avec des exemples PUBLICS (expressions Python entre accents graves, exécutables par
`solution.check` et par l'observation du bras L), des cas CACHÉS dont les valeurs attendues sont calculées par une
implémentation de référence écrite ici, et, selon la catégorie, des fichiers de départ dans l'espace de travail.
Les cas cachés ne recoupent jamais les exemples publics (vérifié par `tests/test_real_validation_harness.py`).

Catégories du banc d'outils (§6.2) :
- `execution` : l'énoncé suffit, mais une erreur de détail se voit en EXÉCUTANT les exemples publics ;
- `test` : des tests unitaires PUBLICS sont fournis dans l'espace (tests/), à lancer ;
- `lecture` : une donnée nécessaire n'existe QUE dans un fichier de l'espace (sans outil : inaccessible) ;
- `modification` : solution.py existe déjà, bogué ; l'énoncé suffit aussi pour réécrire de zéro ;
- `multi` : découvrir (inspect), lire une règle dans un fichier, écrire, valider avec les tests fournis.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ANSWER = " Réponds UNIQUEMENT avec le code Python de la fonction (aucune explication, aucun appel d'exemple)."
#: solution.py est jugé SEUL (sans les autres fichiers de l'espace) : le dire, pour qu'une lecture réussie ne soit
#: pas perdue par un `import` d'un fichier absent au jugement.
SELF_CONTAINED = (" solution.py est jugé seul, sans les autres fichiers : il ne doit rien importer de l'espace de "
                  "travail (recopie dans ton code les valeurs nécessaires).")


@dataclass(frozen=True)
class BenchTask:
    name: str
    category: str
    function: str
    statement: str
    reference: Callable[..., Any]
    hidden: tuple[tuple[Any, ...], ...]
    seed: dict[str, str] = field(default_factory=dict)
    #: fichiers de l'espace qui portent une information NÉCESSAIRE absente de l'énoncé (lecture mesurée).
    needs: tuple[str, ...] = ()

    def cases(self) -> list[dict[str, Any]]:
        return [{"args": list(args), "expected": self.reference(*args)} for args in self.hidden]


# -- références ------------------------------------------------------------------------------------------------


def _slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _compress_ranges(nums: list[int]) -> str:
    values = sorted(set(nums))
    parts, i = [], 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1] == values[j] + 1:
            j += 1
        parts.append(str(values[i]) if i == j else f"{values[i]}-{values[j]}")
        i = j + 1
    return ",".join(parts)


def _parse_kv(s: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for segment in s.split(";"):
        if "=" not in segment:
            continue
        key, value = (x.strip() for x in segment.split("=", 1))
        if not key:
            continue
        out[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
    return out


def _roman(n: int) -> str | None:
    if not isinstance(n, int) or not 1 <= n <= 3999:
        return None
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
             (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for value, symbol in table:
        while n >= value:
            out += symbol
            n -= value
    return out


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _phone(s: str) -> str | None:
    digits = re.sub(r"\D", "", s)
    if s.strip().startswith("+33"):
        digits = "0" + digits[2:]
    if len(digits) != 10 or not digits.startswith("0"):
        return None
    return " ".join(digits[i:i + 2] for i in range(0, 10, 2))


def _grade(score: float) -> str | None:
    if not 0 <= score <= 20:
        return None
    for floor, mention in ((16, "très bien"), (14, "bien"), (12, "assez bien"), (10, "passable")):
        if score >= floor:
            return mention
    return "ajourné"


_TARIFS = {"A": {"base": 4.9, "per_kg": 1.2}, "B": {"base": 7.5, "per_kg": 2.35}, "C": {"base": 12.0, "per_kg": 4.1}}


def _shipping(weight_kg: float, zone: str) -> float | None:
    tariff = _TARIFS.get(zone)
    if tariff is None or weight_kg <= 0:
        return None
    return round(tariff["base"] + tariff["per_kg"] * weight_kg, 2)


def _median(nums: list[float]) -> float | None:
    values = sorted(nums)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


_UNITS = {"km": 1000.0, "m": 1.0, "cm": 0.01, "mm": 0.001, "mi": 1609.344}


def _to_meters(text: str) -> float | None:
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*([a-z]+)\s*", text)
    if not match or match.group(2) not in _UNITS:
        return None
    return round(float(match.group(1)) * _UNITS[match.group(2)], 3)


# -- banc d'outils (§6.2) --------------------------------------------------------------------------------------

TOOL_BENCH: tuple[BenchTask, ...] = (
    BenchTask(
        "slugify", "execution", "slugify",
        "Écris une fonction Python nommée exactement `slugify` qui prend un titre `title` et retourne son « slug » : "
        "minuscules, accents retirés (é donne e), toute suite de caractères qui ne sont ni lettres ni chiffres "
        "remplacée par UN seul tiret, sans tiret au début ni à la fin. Exemples : `slugify('Élan Vital!') == "
        "'elan-vital'`, `slugify('  a  b ') == 'a-b'`, `slugify('Déjà-vu, 2024') == 'deja-vu-2024'`." + ANSWER,
        _slugify, (("Ça marche ?",), ("--Hello__World--",), ("",), ("Noël à Paris",), ("100% sûr!!",),
                   ("crème brûlée",), ("A.B.C",), ("ÉTÉ 2025 / Hiver",))),
    BenchTask(
        "compress_ranges", "execution", "compress_ranges",
        "Écris une fonction Python nommée exactement `compress_ranges` qui prend une liste d'entiers `nums` (dans "
        "un ordre quelconque, avec doublons possibles) et retourne une chaîne décrivant les valeurs distinctes triées, "
        "les suites consécutives étant écrites « début-fin », séparées par des virgules sans espace. Exemples : "
        "`compress_ranges([1, 2, 3, 5]) == '1-3,5'`, `compress_ranges([]) == ''`, "
        "`compress_ranges([4, 2, 3, 3]) == '2-4'`." + ANSWER,
        _compress_ranges, (([7, 8],), ([10],), ([-3, -2, -1, 1],), ([5, 1, 9, 2, 3],), ([0, 0, 0],),
                           ([1, 3, 5, 7],), ([12, 11, 14, 13, 20],), ([-1, 0, 1, 3, 4],))),
    BenchTask(
        "parse_kv", "execution", "parse_kv",
        "Écris une fonction Python nommée exactement `parse_kv` qui lit une chaîne `s` de paires « clé=valeur » "
        "séparées par des points-virgules et retourne un dictionnaire. Les espaces autour des clés et des valeurs "
        "sont retirés ; une valeur faite uniquement de chiffres (avec un « - » initial éventuel) devient un entier, "
        "sinon elle reste une chaîne ; un segment sans « = » ou à clé vide est ignoré ; une clé répétée garde sa "
        "DERNIÈRE valeur. Exemples : `parse_kv('a=1; b = x') == {'a': 1, 'b': 'x'}`, `parse_kv('') == {}`, "
        "`parse_kv('k=1;k=2') == {'k': 2}`." + ANSWER,
        _parse_kv, (("x=-4;y=007",), ("solo",), ("=3;a=b=c",), (" n = 10 ; m = dix ",), ("a=;b=2",),
                    ("p=1;;q=2;p=x",), ("v=1.5",), ("u= -0 ",))),
    BenchTask(
        "roman", "execution", "roman",
        "Écris une fonction Python nommée exactement `roman` qui prend un entier `n` et retourne son écriture en "
        "chiffres romains (notation soustractive : 4 donne IV, 9 donne IX, 40 donne XL…), pour 1 <= n <= 3999 ; "
        "en dehors, retourne None. Exemples : `roman(4) == 'IV'`, `roman(1994) == 'MCMXCIV'`, `roman(0) == None`."
        + ANSWER,
        _roman, ((9,), (14,), (40,), (90,), (400,), (3999,), (4000,), (-5,), (2024,), (444,))),
    BenchTask(
        "wrap_text", "execution", "wrap_text",
        "Écris une fonction Python nommée exactement `wrap_text` qui prend un texte `text` et une largeur `width` "
        "et retourne la liste des lignes obtenues en plaçant les mots (séparés par des blancs) de façon GLOUTONNE : "
        "un mot va sur la ligne courante si la ligne, espace compris, ne dépasse pas `width`, sinon il commence une "
        "nouvelle ligne ; un mot plus long que `width` occupe seul sa ligne, sans être coupé. Exemples : "
        "`wrap_text('a bb ccc', 4) == ['a bb', 'ccc']`, `wrap_text('', 5) == []`, "
        "`wrap_text('immense mot', 3) == ['immense', 'mot']`." + ANSWER,
        _wrap, (("le petit chat dort", 9), ("un deux trois", 100), ("a b c d e", 3), ("  espaces   multiples ", 8),
                ("xyz", 1), ("ab cd ef gh", 5), ("mot", 3), ("aa bb cc", 5))),
    BenchTask(
        "normalize_phone", "test", "normalize_phone",
        "Écris une fonction Python nommée exactement `normalize_phone` qui normalise un numéro de téléphone français "
        "`s` : seuls les chiffres comptent ; un préfixe « +33 » remplace le 0 initial ; le résultat doit faire "
        "exactement 10 chiffres commençant par 0, écrits par paires séparées par une espace, sinon None. Des tests "
        "unitaires publics sont fournis dans tests/test_solution.py : fais-les passer. Exemples : "
        "`normalize_phone('06.12.34.56.78') == '06 12 34 56 78'`, `normalize_phone('+33 6 12 34 56 78') == "
        "'06 12 34 56 78'`." + ANSWER,
        _phone, (("01-23-45-67-89",), ("+33123456789",), ("12345",), ("0612345678 ",), (" 07 00 00 00 01",),
                 ("99 99 99 99 99",), ("06 12 34 56 7",), ("",)),
        seed={"tests/test_solution.py": (
            "import unittest\nfrom solution import normalize_phone\n\n\nclass T(unittest.TestCase):\n"
            "    def test_points(self):\n        self.assertEqual(normalize_phone('06.12.34.56.78'), '06 12 34 56 78')\n\n"
            "    def test_plus33(self):\n        self.assertEqual(normalize_phone('+33 6 12 34 56 78'), '06 12 34 56 78')\n\n"
            "    def test_trop_court(self):\n        self.assertIsNone(normalize_phone('06 12'))\n\n"
            "    def test_pas_un_zero(self):\n        self.assertIsNone(normalize_phone('16 12 34 56 78'))\n")}),
    BenchTask(
        "grade", "test", "grade",
        "Écris une fonction Python nommée exactement `grade` qui prend une note `score` sur 20 et retourne la "
        "mention : « très bien » à partir de 16, « bien » à partir de 14, « assez bien » à partir de 12, "
        "« passable » à partir de 10, « ajourné » en dessous ; une note hors de [0, 20] donne None. Des tests "
        "unitaires publics sont fournis dans tests/test_solution.py : fais-les passer. Exemples : "
        "`grade(15) == 'bien'`, `grade(9.5) == 'ajourné'`." + ANSWER,
        _grade, ((16,), (14,), (12.0,), (10,), (0,), (20,), (20.5,), (-1,), (13.99,), (17.5,)),
        seed={"tests/test_solution.py": (
            "import unittest\nfrom solution import grade\n\n\nclass T(unittest.TestCase):\n"
            "    def test_bien(self):\n        self.assertEqual(grade(15), 'bien')\n\n"
            "    def test_ajourne(self):\n        self.assertEqual(grade(9.5), 'ajourné')\n\n"
            "    def test_bornes(self):\n        self.assertEqual(grade(16), 'très bien')\n"
            "        self.assertEqual(grade(10), 'passable')\n\n"
            "    def test_hors_bornes(self):\n        self.assertIsNone(grade(21))\n")}),
    BenchTask(
        "shipping_cost", "lecture", "shipping_cost",
        "Écris une fonction Python nommée exactement `shipping_cost` qui prend un poids `weight_kg` et une zone "
        "`zone` et retourne le coût d'expédition : forfait de base de la zone plus prix par kilo de la zone fois le "
        "poids, arrondi à 2 décimales. Les tarifs de chaque zone sont UNIQUEMENT dans le fichier data/tarifs.json de "
        "l'espace de travail. Zone inconnue ou poids <= 0 : None. Exemple : `shipping_cost(0, 'A') == None`."
        + SELF_CONTAINED + ANSWER,
        _shipping, ((1, "A"), (4, "B"), (10, "C"), (3, "Z"), (0.5, "A"), (-1, "B"), (7, "B"), (2, "C")),
        seed={"data/tarifs.json": json.dumps(_TARIFS, indent=1)}, needs=("data/tarifs.json",)),
    BenchTask(
        "median_fix", "modification", "median",
        "Le fichier solution.py contient une fonction `median` qui donne des résultats faux. Corrige-la : `median` "
        "prend une liste de nombres `nums` (NON triée) et retourne la médiane (valeur du milieu après tri, ou "
        "MOYENNE des deux valeurs du milieu pour un nombre pair d'éléments) ; une liste vide donne None. Exemples : "
        "`median([3, 1, 2]) == 2`, `median([4, 1, 3, 2]) == 2.5`, `median([]) == None`." + ANSWER,
        _median, (([5],), ([9, 1],), ([7, 3, 5, 1],), ([2, 2, 2],), ([-1, -5, 3],), ([10, 0, 5, 5, 20, 1],),
                  ([1.5, 0.5],), ([100, 1, 50],))),
    BenchTask(
        "to_meters", "multi", "to_meters",
        "Écris une fonction Python nommée exactement `to_meters` qui convertit une longueur écrite `text` (un nombre, "
        "éventuellement décimal ou négatif, puis une unité, espaces tolérés) en mètres, arrondie à 3 décimales. Les "
        "unités acceptées et leurs facteurs sont définis dans le module helpers/units.py de l'espace de travail ; "
        "une unité absente de ce module ou un texte mal formé donne None. Des tests publics sont fournis dans "
        "tests/. Exemple : `to_meters('abc') == None`." + SELF_CONTAINED + ANSWER,
        _to_meters, (("2 km",), ("150cm",), ("3.5 mi",), ("12 ft",), (" -4 m ",), ("1.25km",), ("7 mm",), ("km",)),
        seed={"helpers/__init__.py": "",
              "helpers/units.py": ("# Facteurs de conversion vers le mètre (unités acceptées).\n"
                                   "UNITS = {\n    'km': 1000.0,\n    'm': 1.0,\n    'cm': 0.01,\n"
                                   "    'mm': 0.001,\n    'mi': 1609.344,\n}\n"),
              "tests/test_solution.py": (
                  "import unittest\nfrom solution import to_meters\n\n\nclass T(unittest.TestCase):\n"
                  "    def test_metres(self):\n        self.assertEqual(to_meters('3 m'), 3.0)\n\n"
                  "    def test_mal_forme(self):\n        self.assertIsNone(to_meters('abc'))\n")},
        needs=("helpers/units.py",)),
)

#: solution.py BOGUÉ de départ de la tâche « modification » (pas trié, cas pair faux).
TOOL_BENCH_SEED_SOLUTION = {"median_fix": "def median(nums):\n    if not nums:\n        return None\n"
                                          "    return nums[len(nums) // 2]\n"}


def bench_spec(task: BenchTask, *, with_seed: bool) -> Any:
    """`SolutionTaskSpec` d'une tâche du banc ; `with_seed=False` : sans fichiers de départ (bras sans outils)."""
    from dataclasses import replace

    from apex.tasks.solution_tasks import build_user_function_task_spec

    spec = build_user_function_task_spec(function=task.function, description=task.statement, cases=task.cases(),
                                         title=f"Banc — {task.name}")
    seed = dict(task.seed)
    if task.name in TOOL_BENCH_SEED_SOLUTION:
        seed["solution.py"] = TOOL_BENCH_SEED_SOLUTION[task.name]
    return replace(spec, workspace_seed=seed if (with_seed and seed) else None)
