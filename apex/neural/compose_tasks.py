"""Exercices de programmation COMPOSÉS — des milliers de fonctions différentes au lieu d'une cinquantaine.

Une fonction = un filtre (facultatif), une transformation (facultative) et un agrégat, sur une liste de
nombres ou sur les mots d'un texte : « renvoie la somme des valeurs obtenues en élevant au carré chaque
nombre pair de la liste ». Le code de référence est écrit par le générateur ; les résultats attendus des
tests sont obtenus en l'EXÉCUTANT (vrais par construction).

Évaluation de la GÉNÉRALISATION : environ 15 % des combinaisons (filtre, transformation, agrégat) ne sont
jamais montrées à l'entraînement — chaque pièce est connue, leur assemblage ne l'est pas
(`code_compose_heldout`) ; `code_compose` mesure les combinaisons vues avec d'autres constantes.
"""

# Tirages reproductibles d'exercices, pas de cryptographie ; textes français (guillemets, apostrophes).
# ruff: noqa: S311

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apex.neural.tasks import TaskExample, with_system


@dataclass(frozen=True)
class _Piece:
    key: str
    fr: str
    en: str
    code: str


def _numbers_filters(k: int, m: int) -> list[_Piece | None]:
    return [
        None,
        _Piece("pairs", "pair", "even", "x % 2 == 0"),
        _Piece("impairs", "impair", "odd", "x % 2 != 0"),
        _Piece("positifs", "strictement positif", "strictly positive", "x > 0"),
        _Piece("negatifs", "strictement négatif", "strictly negative", "x < 0"),
        _Piece(f"sup_{k}", f"supérieur à {k}", f"greater than {k}", f"x > {k}"),
        _Piece(f"inf_{k}", f"inférieur à {k}", f"less than {k}", f"x < {k}"),
        _Piece(f"multiples_{m}", f"multiple de {m}", f"multiple of {m}", f"x % {m} == 0"),
        _Piece(f"sauf_{k}", f"différent de {k}", f"different from {k}", f"x != {k}"),
    ]


def _numbers_maps(k: int) -> list[_Piece | None]:
    return [
        None,
        _Piece("carres", "élevant au carré", "squaring", "x * x"),
        _Piece("doubles", "doublant", "doubling", "2 * x"),
        _Piece(f"fois_{k}", f"multipliant par {k}", f"multiplying by {k}", f"x * {k}"),
        _Piece(f"plus_{k}", f"ajoutant {k} à", f"adding {k} to", f"x + {k}"),
        _Piece(f"moins_{k}", f"retirant {k} à", f"subtracting {k} from", f"x - {k}"),
        _Piece("absolus", "prenant la valeur absolue de", "taking the absolute value of", "abs(x)"),
        _Piece("opposes", "prenant l'opposé de", "negating", "-x"),
    ]


@dataclass(frozen=True)
class _Reducer:
    key: str
    masculine: str  # devant « nombres »
    feminine: str  # devant « valeurs »
    en: str
    note_fr: str
    note_en: str
    code: str


_NUMBER_REDUCERS = (
    _Reducer("somme", "la somme", "la somme", "the sum", "", "", "    return sum(valeurs)\n"),
    _Reducer("nombre", "le nombre", "le nombre", "the count", "", "", "    return len(valeurs)\n"),
    _Reducer("liste", "la liste", "la liste", "the list", "", "", "    return valeurs\n"),
    _Reducer(
        "produit",
        "le produit",
        "le produit",
        "the product",
        " (1 s'il n'y en a aucun)",
        " (1 if there are none)",
        "    produit = 1\n    for v in valeurs:\n        produit *= v\n    return produit\n",
    ),
    _Reducer(
        "max",
        "le plus grand",
        "la plus grande",
        "the largest",
        " (None s'il n'y en a aucun)",
        " (None if there are none)",
        "    return max(valeurs) if valeurs else None\n",
    ),
    _Reducer(
        "min",
        "le plus petit",
        "la plus petite",
        "the smallest",
        " (None s'il n'y en a aucun)",
        " (None if there are none)",
        "    return min(valeurs) if valeurs else None\n",
    ),
)
#: Filtres placés avant le nom en anglais (« each even number »).
_EN_BEFORE = frozenset({"pairs", "impairs", "positifs", "negatifs"})


@dataclass(frozen=True)
class _WordFilter:
    key: str
    singular: str
    plural: str
    en: str
    code: str


def _word_filters(n: int, letter: str) -> list[_WordFilter | None]:
    return [
        None,
        _WordFilter(
            f"longs_{n}",
            f"qui fait plus de {n} lettres",
            f"qui font plus de {n} lettres",
            f"longer than {n} letters",
            f"len(m) > {n}",
        ),
        _WordFilter(
            f"courts_{n}",
            f"qui fait au plus {n} lettres",
            f"qui font au plus {n} lettres",
            f"at most {n} letters long",
            f"len(m) <= {n}",
        ),
        _WordFilter(
            f"debut_{letter}",
            f"qui commence par « {letter} »",
            f"qui commencent par « {letter} »",
            f'starting with "{letter}"',
            f"m.startswith({letter!r})",
        ),
        _WordFilter(
            f"avec_{letter}",
            f"qui contient « {letter} »",
            f"qui contiennent « {letter} »",
            f'containing "{letter}"',
            f"{letter!r} in m",
        ),
    ]


_WORD_MAPS: tuple[_Piece | None, ...] = (
    None,
    _Piece("majuscules", "mettant en majuscules", "uppercasing", "m.upper()"),
    _Piece("envers", "retournant", "reversing", "m[::-1]"),
    _Piece("initiale", "gardant la première lettre de", "keeping the first letter of", "m[0]"),
    _Piece("capitale", "mettant une majuscule à", "capitalizing", "m.capitalize()"),
)

_WORD_REDUCERS = (
    _Piece(
        "phrase",
        "réunis en un seul texte, séparés par des espaces",
        "joined into one text separated by spaces",
        '    return " ".join(valeurs)\n',
    ),
    _Piece("nombre", "le nombre", "the count", "    return len(valeurs)\n"),
    _Piece("liste", "la liste", "the list", "    return valeurs\n"),
)

_WORDS = (
    "le chat dort sur le tapis rouge",
    "python est un langage simple et puissant",
    "apex apprend vite et bien",
    "the quick brown fox jumps over the lazy dog",
    "un serveur une base et une interface",
    "code tests revue puis livraison",
    "a small model can still be useful",
    "bonjour tout le monde",
    "",
    "alpha beta gamma delta epsilon",
)


def _is_heldout(kind: str, filter_key: str, map_key: str, reducer_key: str) -> bool:
    """Combinaison réservée à l'évaluation (≈ 15 %), décidée par empreinte : stable, indépendante des
    tirages. Les constantes ne comptent pas : « supérieur à 5 » et « supérieur à 9 » sont la même pièce."""
    pieces = "|".join((kind, filter_key.split("_")[0], map_key.split("_")[0], reducer_key))
    return int(hashlib.sha256(pieces.encode()).hexdigest()[:8], 16) % 100 < 15


@dataclass(frozen=True)
class _Composed:
    name: str
    params: str
    fr: str
    en: str
    code: str
    inputs: tuple[Any, ...]
    heldout: bool


def _number_function(rng: random.Random) -> _Composed:
    k, m = rng.randint(1, 9), rng.choice((3, 4, 5, 6, 7))
    filt = rng.choice(_numbers_filters(k, m))
    mapping = rng.choice(_numbers_maps(rng.randint(2, 9)))
    reducer = rng.choice(_NUMBER_REDUCERS)
    keys = (filt.key if filt else "tous", mapping.key if mapping else "valeurs", reducer.key)
    name = f"{keys[2]}_{keys[1]}_{keys[0]}"
    if filt is None:
        each_fr, each_en, which_fr, which_en = "chaque nombre", "each number", "", ""
    else:
        each_fr = f"chaque nombre {filt.fr}"
        each_en = f"each {filt.en} number" if filt.key in _EN_BEFORE else f"each number {filt.en}"
        which_fr, which_en = f" qui sont {_fr_plural(filt.fr)}", f" that are {filt.en}"
    if mapping:
        fr = (
            f"renvoie {reducer.feminine} des valeurs obtenues en {mapping.fr} {each_fr} de la liste "
            f"nombres{reducer.note_fr.replace('aucun', 'aucune')}"
        )
        en = (
            f"returns {reducer.en} of the values obtained by {mapping.en} {each_en} in the list "
            f"nombres{reducer.note_en}"
        )
    else:
        fr = f"renvoie {reducer.masculine} des nombres de la liste nombres{which_fr}{reducer.note_fr}"
        en = f"returns {reducer.en} of the numbers in the list nombres{which_en}{reducer.note_en}"
    expression = mapping.code if mapping else "x"
    condition = f" if {filt.code}" if filt else ""
    code = f"def {name}(nombres):\n    valeurs = [{expression} for x in nombres{condition}]\n{reducer.code}"
    inputs = ([], *([rng.randint(-15, 25) for _ in range(rng.randint(1, 8))] for _ in range(4)))
    return _Composed(
        name, "nombres", fr, en, code, tuple((i,) for i in inputs), _is_heldout("nombres", *keys)
    )


def _fr_plural(phrase: str) -> str:
    first, _, rest = phrase.partition(" ")
    if first == "strictement":
        second, _, tail = rest.partition(" ")
        return f"strictement {second}s" + (f" {tail}" if tail else "")
    return f"{first}s" + (f" {rest}" if rest else "")


def _word_function(rng: random.Random) -> _Composed:
    filt = rng.choice(_word_filters(rng.randint(2, 5), rng.choice("aeilmnoprst")))
    mapping = rng.choice(_WORD_MAPS)
    reducer = rng.choice(_WORD_REDUCERS)
    keys = (filt.key if filt else "tous", mapping.key if mapping else "mots", reducer.key)
    name = f"{keys[2]}_{keys[1]}_{keys[0]}"
    if mapping:
        words_fr = (
            f"les mots obtenus en {mapping.fr} chaque mot{' ' + filt.singular if filt else ''} du texte"
        )
        words_en = (
            f"the words obtained by {mapping.en} each word{' ' + filt.en if filt else ''} of the text"
        )
    else:
        words_fr = f"les mots du texte{' ' + filt.plural if filt else ''}"
        words_en = f"the words of the text{' ' + filt.en if filt else ''}"
    if reducer.key == "phrase":
        fr, en = f"renvoie {words_fr}, {reducer.fr}", f"returns {words_en}, {reducer.en}"
    else:
        fr = f"renvoie {reducer.fr} des {words_fr.removeprefix('les ')}"
        en = f"returns {reducer.en} of {words_en}"
    expression = mapping.code if mapping else "m"
    condition = f" if {filt.code}" if filt else ""
    body = f"    valeurs = [{expression} for m in texte.split(){condition}]\n"
    code = f"def {name}(texte):\n{body}{reducer.code}"
    inputs = tuple((rng.choice(_WORDS),) for _ in range(5))
    return _Composed(name, "texte", fr, en, code, inputs, _is_heldout("texte", *keys))


def _run(code: str, name: str, args: tuple[Any, ...]) -> Any:
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102 - code écrit par ce générateur, jamais d'origine extérieure
    return namespace[name](*args)


def _example(rng: random.Random, lang: str, function: _Composed, family: str) -> TaskExample:
    description = function.fr if lang == "fr" else function.en
    signature = f"{function.name}({function.params})"
    if lang == "fr":
        user = rng.choice(
            (
                f"Écris une fonction Python `{signature}` qui {description}.",
                f"Écris en Python la fonction {signature} : elle {description}.",
            )
        )
    else:
        user = rng.choice(
            (
                f"Write a Python function `{signature}` that {description}.",
                f"Write the Python function {signature}: it {description}.",
            )
        )
    tests = [[list(args), _run(function.code, function.name, args)] for args in function.inputs]
    return TaskExample(
        family,
        lang,
        user,
        f"```python\n{function.code}```",
        {"type": "code", "function": function.name, "tests": tests},
        meta={"function": function.name, "heldout": function.heldout},
    )


def compose_examples(
    count: int, *, seed: int, split: str, exclude: frozenset[str] = frozenset()
) -> list[TaskExample]:
    """`split="train"` : `count` exercices, jamais une combinaison réservée. `split="eval"` : `count`
    exercices de combinaisons vues (`code_compose`) puis `count` de combinaisons réservées
    (`code_compose_heldout`). `exclude` : codes à ne jamais produire (ceux de l'évaluation)."""
    rng = random.Random(f"compose:{split}:{seed}")
    makers: tuple[Callable[[random.Random], _Composed], ...] = (
        _number_function,
        _number_function,
        _word_function,
    )
    wanted = (
        {"code_compose": count}
        if split == "train"
        else {"code_compose": count, "code_compose_heldout": count}
    )
    out: list[TaskExample] = []
    seen: set[str] = set()
    while any(wanted.values()):
        function = rng.choice(makers)(rng)
        family = "code_compose_heldout" if function.heldout else "code_compose"
        if (
            (split == "train" and function.heldout)
            or wanted.get(family, 0) <= 0
            or function.code in seen
            or function.code in exclude
        ):
            continue
        seen.add(function.code)
        wanted[family] -= 1
        out.append(with_system(_example(rng, rng.choice(("fr", "en")), function, family), rng))
    return out
