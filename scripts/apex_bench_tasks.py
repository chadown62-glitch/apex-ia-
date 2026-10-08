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


# -- banc mémoire (§6.3) : l'information nécessaire n'existe QUE dans une expérience passée ----------------------
#
# Chaque paire : une tâche SOURCE dont l'énoncé donne explicitement une convention « maison » (tarifs, alphabet,
# codes promo, unités, barème, format de date), puis une tâche CIBLE différente (autre fonction, autres entrées)
# qui s'y réfère par son nom SANS la redonner — comme un utilisateur dit « avec les mêmes règles que la dernière
# fois ». Sans mémoire, la convention est introuvable ; avec la mémoire, elle est dans l'expérience de la source
# (énoncé ET code produits par APEX lui-même — jamais une donnée écrite par le banc dans la mémoire). Les entrées
# des cas cachés de la cible n'apparaissent nulle part dans la source (vérifié par les tests).


@dataclass(frozen=True)
class MemoryPair:
    name: str
    source: BenchTask
    target: BenchTask


_ZEPHYR = {"A": (4.9, 1.2), "B": (7.5, 2.35), "C": (12.0, 4.1)}


def _zephyr_shipping(weight_kg: float, zone: str) -> float | None:
    if zone not in _ZEPHYR or weight_kg <= 0:
        return None
    base, per_kg = _ZEPHYR[zone]
    return round(base + per_kg * weight_kg, 2)


def _zephyr_order_total(parcels: list[list[Any]]) -> float:
    costs = [_zephyr_shipping(w, z) for w, z in parcels]
    return round(sum(c for c in costs if c is not None), 2)


_KAPPA = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _kappa_encode(n: int) -> str:
    if n == 0:
        return _KAPPA[0]
    out = ""
    while n:
        n, r = divmod(n, 32)
        out = _KAPPA[r] + out
    return out


def _kappa_decode(s: str) -> int | None:
    if not s or any(c not in _KAPPA for c in s):
        return None
    value = 0
    for c in s:
        value = value * 32 + _KAPPA.index(c)
    return value


_LUMEN = {"ETE10": ("pct", 10), "VIP25": ("pct", 25), "BIENVENUE": ("abs", 5)}


def _lumen_discount(total: float, code: str) -> float:
    kind, amount = _LUMEN.get(code, ("none", 0))
    if kind == "pct":
        total = total * (100 - amount) / 100
    elif kind == "abs":
        total = max(0.0, total - amount)
    return round(total, 2)


def _lumen_cart(prices: list[float], code: str) -> float:
    return _lumen_discount(sum(prices), code) if prices else 0


_ORION = {"pas": 0.8, "perche": 5.0292, "lieue": 4828.032, "toise": 1.949}


def _orion_to_meters(value: float, unit: str) -> float | None:
    return round(value * _ORION[unit], 3) if unit in _ORION else None


def _orion_convert(value: float, from_unit: str, to_unit: str) -> float | None:
    if from_unit not in _ORION or to_unit not in _ORION:
        return None
    return round(value * _ORION[from_unit] / _ORION[to_unit], 3)


def _delta_mention(score: float) -> str | None:
    if not 0 <= score <= 20:
        return None
    for floor, mention in ((17, "excellent"), (13.5, "solide"), (9, "juste")):
        if score >= floor:
            return mention
    return "insuffisant"


def _delta_report(scores: list[float]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in scores:
        mention = _delta_mention(s)
        if mention is not None:
            out[mention] = out.get(mention, 0) + 1
    return out


_NOVA_MONTHS = ("JAN", "FEV", "MAR", "AVR", "MAI", "JUN", "JUL", "AOU", "SEP", "OCT", "NOV", "DEC")


def _nova_date(year: int, month: int, day: int) -> str:
    return f"{day:02d}.{_NOVA_MONTHS[month - 1]}.{year:04d}"


def _nova_parse(s: str) -> list[int] | None:
    match = re.fullmatch(r"(\d{2})\.([A-Z]{3})\.(\d{4})", s)
    if not match or match.group(2) not in _NOVA_MONTHS:
        return None
    return [int(match.group(3)), _NOVA_MONTHS.index(match.group(2)) + 1, int(match.group(1))]


MEMORY_BENCH: tuple[MemoryPair, ...] = (
    MemoryPair("zephyr", BenchTask(
        "zephyr_shipping", "source", "zephyr_shipping",
        "Écris une fonction Python nommée exactement `zephyr_shipping` qui prend un poids `weight_kg` et une zone "
        "`zone` et retourne le coût d'expédition selon les tarifs de la société Zéphyr : zone 'A' : forfait 4.90 "
        "plus 1.20 par kilo ; zone 'B' : forfait 7.50 plus 2.35 par kilo ; zone 'C' : forfait 12.00 plus 4.10 par "
        "kilo. Arrondi à 2 décimales ; zone inconnue ou poids <= 0 : None. Exemple : "
        "`zephyr_shipping(1, 'A') == 6.1`." + ANSWER,
        _zephyr_shipping, ((2, "A"), (3, "B"), (1, "C"), (5, "Z"), (0, "B"), (10, "A"), (4, "C"), (6, "B"))),
        BenchTask(
        "zephyr_order_total", "target", "zephyr_order_total",
        "Écris une fonction Python nommée exactement `zephyr_order_total` qui prend une liste de colis `parcels` "
        "(chaque colis est une liste [poids, zone]) et retourne le coût total d'expédition de la commande au tarif "
        "d'expédition de la société Zéphyr — le même tarif que pour la tâche zephyr_shipping déjà traitée par "
        "APEX. Un colis invalide (zone inconnue ou poids <= 0) est ignoré ; total arrondi à 2 décimales. "
        "Exemple : `zephyr_order_total([]) == 0`." + ANSWER,
        _zephyr_order_total, (([[1.5, "A"], [2, "B"]],), ([[7, "C"]],), ([[1, "B"], [9, "Q"]],),
                              ([[8, "A"], [8, "A"]],), ([[2.5, "C"], [-1, "A"], [12, "B"]],), ([[20, "B"]],),
                              ([[0, "A"]],), ([[3.5, "A"], [1.5, "C"]],)))),
    MemoryPair("kappa", BenchTask(
        "kappa_encode", "source", "kappa_encode",
        "Écris une fonction Python nommée exactement `kappa_encode` qui encode un entier `n` >= 0 en identifiant "
        "Kappa : écriture en base 32 avec l'alphabet Kappa '23456789ABCDEFGHJKLMNPQRSTUVWXYZ' (le symbole de rang "
        "0 est '2', celui de rang 31 est 'Z'), symbole de poids fort en premier ; 0 donne '2'. Exemples : "
        "`kappa_encode(0) == '2'`, `kappa_encode(33) == '33'`." + ANSWER,
        _kappa_encode, ((1,), (31,), (32,), (1000,), (123456,), (7,), (1023,), (99999,))),
        BenchTask(
        "kappa_decode", "target", "kappa_decode",
        "Écris une fonction Python nommée exactement `kappa_decode` qui fait l'inverse de l'encodage des "
        "identifiants Kappa de la tâche kappa_encode déjà traitée par APEX (même alphabet, même ordre) : elle "
        "prend une chaîne `s` et retourne l'entier ; une chaîne vide ou contenant un symbole hors de l'alphabet "
        "Kappa donne None. Exemple : `kappa_decode('') == None`." + ANSWER,
        _kappa_decode, (("2",), ("Z",), ("32",), ("ZZ",), ("A7",), ("1",), ("HELLO",), ("K2M",)))),
    MemoryPair("lumen", BenchTask(
        "lumen_discount", "source", "lumen_discount",
        "Écris une fonction Python nommée exactement `lumen_discount` qui applique un code promo de la boutique "
        "Lumen à un montant `total` : 'ETE10' retire 10 % ; 'VIP25' retire 25 % ; 'BIENVENUE' retire 5 (sans "
        "descendre sous 0) ; tout autre code laisse le montant inchangé. Résultat arrondi à 2 décimales. "
        "Exemple : `lumen_discount(100, 'ETE10') == 90.0`." + ANSWER,
        _lumen_discount, ((80, "VIP25"), (3, "BIENVENUE"), (50, "BIENVENUE"), (19.99, "ETE10"), (40, "SOLDES"),
                          (0, "VIP25"), (12.4, "VIP25"), (100, "ete10"))),
        BenchTask(
        "lumen_cart", "target", "lumen_cart",
        "Écris une fonction Python nommée exactement `lumen_cart` qui prend une liste de prix `prices` et un code "
        "promo `code` et retourne le montant à payer : la somme des prix, à laquelle on applique le code promo de "
        "la boutique Lumen — mêmes codes et mêmes règles que pour la tâche lumen_discount déjà traitée par APEX. "
        "Résultat arrondi à 2 décimales ; panier vide : 0. Exemple : `lumen_cart([], 'X') == 0`." + ANSWER,
        _lumen_cart, (([10, 20, 30], "ETE10"), ([2, 1.5], "BIENVENUE"), ([100], "VIP25"), ([9.99, 0.01], "NOEL"),
                      ([60, 40], "BIENVENUE"), ([33.33], "VIP25"), ([5], "ETE10"), ([1, 1, 1], "BIENVENUE")))),
    MemoryPair("orion", BenchTask(
        "orion_to_meters", "source", "orion_to_meters",
        "Écris une fonction Python nommée exactement `orion_to_meters` qui convertit une longueur `value` exprimée "
        "dans une unité du système Orion `unit` en mètres : 'pas' vaut 0.8 m ; 'perche' vaut 5.0292 m ; 'lieue' "
        "vaut 4828.032 m ; 'toise' vaut 1.949 m ; toute autre unité donne None. Arrondi à 3 décimales. Exemple : "
        "`orion_to_meters(10, 'pas') == 8.0`." + ANSWER,
        _orion_to_meters, ((2, "lieue"), (3, "perche"), (2, "toise"), (7, "pas"), (1, "mile"), (0, "toise"),
                           (12, "perche"), (0.25, "lieue"))),
        BenchTask(
        "orion_convert", "target", "orion_convert",
        "Écris une fonction Python nommée exactement `orion_convert` qui convertit une longueur `value` d'une "
        "unité Orion `from_unit` vers une autre unité Orion `to_unit` — mêmes unités et mêmes valeurs en mètres "
        "que pour la tâche orion_to_meters déjà traitée par APEX. Arrondi à 3 décimales ; une unité inconnue "
        "donne None. Exemple : `orion_convert(1, 'x', 'pas') == None`." + ANSWER,
        _orion_convert, ((1, "lieue", "pas"), (10, "toise", "perche"), (100, "pas", "toise"), (4, "perche", "pas"),
                         (2, "pas", "mile"), (1, "toise", "toise"), (5000, "pas", "lieue"), (7, "perche", "toise")))),
    MemoryPair("delta", BenchTask(
        "delta_mention", "source", "delta_mention",
        "Écris une fonction Python nommée exactement `delta_mention` qui donne la mention d'une note `score` sur "
        "20 selon le barème Delta : 'excellent' à partir de 17 ; 'solide' à partir de 13.5 ; 'juste' à partir de "
        "9 ; 'insuffisant' en dessous ; une note hors de [0, 20] donne None. Exemple : "
        "`delta_mention(18) == 'excellent'`." + ANSWER,
        _delta_mention, ((17,), (13.5,), (13.4,), (9,), (8.99,), (0,), (20.5,), (15,))),
        BenchTask(
        "delta_report", "target", "delta_report",
        "Écris une fonction Python nommée exactement `delta_report` qui prend une liste de notes `scores` sur 20 "
        "et retourne un dictionnaire {mention: nombre de notes} selon le barème Delta — le même barème que pour "
        "la tâche delta_mention déjà traitée par APEX ; les notes hors de [0, 20] sont ignorées et seules les "
        "mentions présentes figurent. Exemple : `delta_report([]) == {}`." + ANSWER,
        _delta_report, (([14, 9.5, 2],), ([17.5, 17, 16.9],), ([13.5, 13.4, 9, 8.9],), ([21, -1],), ([10, 10],),
                        ([0, 20],), ([16, 12, 8, 19],), ([13.6],)))),
    MemoryPair("nova", BenchTask(
        "nova_date", "source", "nova_date",
        "Écris une fonction Python nommée exactement `nova_date` qui formate une date au format Nova : jour sur 2 "
        "chiffres, un point, le mois en 3 lettres majuscules (JAN FEV MAR AVR MAI JUN JUL AOU SEP OCT NOV DEC), "
        "un point, l'année sur 4 chiffres. Elle prend `year`, `month` (1 à 12) et `day`. Exemple : "
        "`nova_date(2024, 3, 5) == '05.MAR.2024'`." + ANSWER,
        _nova_date, ((1999, 12, 31), (2025, 8, 1), (2000, 2, 29), (1987, 6, 15), (2030, 7, 4), (2024, 4, 10),
                     (2011, 1, 9), (2022, 10, 22))),
        BenchTask(
        "nova_parse", "target", "nova_parse",
        "Écris une fonction Python nommée exactement `nova_parse` qui fait l'inverse du format de date Nova de la "
        "tâche nova_date déjà traitée par APEX : elle prend une chaîne `s` et retourne la liste [année, mois, "
        "jour] en entiers ; un texte qui ne respecte pas exactement le format Nova donne None. Exemple : "
        "`nova_parse('') == None`." + ANSWER,
        _nova_parse, (("14.JUL.1789",), ("01.AOU.2020",), ("25.DEC.2023",), ("07.FEV.1999",), ("31.12.2023",),
                      ("05.mar.2024",), ("09.SEP.2001",), ("5.MAI.2024",)))),
)


def memory_spec(task: BenchTask) -> Any:
    from apex.tasks.solution_tasks import build_user_function_task_spec

    return build_user_function_task_spec(function=task.function, description=task.statement, cases=task.cases(),
                                         title=f"Banc mémoire — {task.name}")
