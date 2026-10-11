"""Calculatrice EXACTE du Cerveau APEX — l'outil que le modèle appelle au lieu de calculer « de tête ».

Le modèle écrit une expression (`974*89`, `sorted([7, -2, 5])`, `weekday("16 avril 2020")`) entre
`<|tool_call|>` et `<|tool_result|>` ; le moteur d'inférence l'évalue ici et place le résultat dans la
réponse : le modèle décide QUOI calculer, le calcul lui-même est exact.

Sûreté : l'expression est analysée (arbre syntaxique Python) et SEULS sont acceptés les nombres, les textes
et listes littéraux, les opérateurs `+ - * / // % **` entre nombres et les fonctions PURES de `FUNCTIONS`
(listes, texte, calendrier) — aucun autre nom, aucun attribut, aucun argument nommé : rien d'autre ne
s'exécute, ni fichier, ni réseau, ni système. Bornes : longueur de l'expression, des textes et des listes,
exposant, taille des nombres (un calcul démesuré est refusé, jamais bloquant).
"""

from __future__ import annotations

import ast
import datetime as dt
import math
import operator
import re
from collections.abc import Callable
from typing import Any

MAX_EXPRESSION_CHARS = 300
MAX_EXPONENT = 64
MAX_DIGITS = 60
MAX_TEXT = 200
MAX_ITEMS = 64

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}

MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
DAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class CalcError(ValueError):
    """Expression refusée ou incalculable — le message est montré au modèle à la place du résultat."""


def _check_size(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise CalcError("résultat infini")
    if isinstance(value, int) and len(str(abs(value))) > MAX_DIGITS:
        raise CalcError("nombre trop grand")
    if isinstance(value, str) and len(value) > MAX_TEXT:
        raise CalcError("texte trop long")
    if isinstance(value, list) and len(value) > MAX_ITEMS:
        raise CalcError("liste trop longue")
    return value


# ------------------------------------------------------------------------------------- fonctions permises


def _numbers(values: Any) -> list[int | float]:
    if not isinstance(values, list) or not all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in values
    ):
        raise CalcError("une liste de nombres est attendue")
    return values


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise CalcError("un texte entre guillemets est attendu")
    return value


def _lang(value: Any) -> str:
    if value not in ("fr", "en"):
        raise CalcError('langue attendue : "fr" ou "en"')
    return str(value)


def parse_date(text: Any) -> tuple[dt.date, str]:
    """« 16 avril 2020 » (français), « April 16, 2020 » (anglais) ou « 2020-04-16 » → (date, langue)."""
    value = " ".join(_text(text).split())
    try:
        if match := re.fullmatch(r"(\d{1,2})(?:er)? (\w+) (\d{4})", value):
            month = MONTHS_FR.index(match.group(2).lower()) + 1
            return dt.date(int(match.group(3)), month, int(match.group(1))), "fr"
        if match := re.fullmatch(r"(\w+) (\d{1,2}), (\d{4})", value):
            month = [m.lower() for m in MONTHS_EN].index(match.group(1).lower()) + 1
            return dt.date(int(match.group(3)), month, int(match.group(2))), "en"
        return dt.date.fromisoformat(value), "fr"
    except ValueError as exc:
        raise CalcError(f"date illisible : {value}") from exc


def show_date(day: dt.date, lang: str) -> str:
    return (
        f"{day.day} {MONTHS_FR[day.month - 1]} {day.year}"
        if lang == "fr"
        else f"{MONTHS_EN[day.month - 1]} {day.day}, {day.year}"
    )


def _add_days(text: Any, days: Any) -> str:
    start, lang = parse_date(text)
    if not isinstance(days, int) or abs(days) > 100_000:
        raise CalcError("nombre de jours invalide")
    return show_date(start + dt.timedelta(days=days), lang)


def _weekday(text: Any) -> str:
    day, lang = parse_date(text)
    return (DAYS_FR if lang == "fr" else DAYS_EN)[day.weekday()]


def _days_between(first: Any, second: Any) -> int:
    return abs((parse_date(second)[0] - parse_date(first)[0]).days)


def _is_leap(year: Any, lang: Any = "fr") -> str:
    if not isinstance(year, int):
        raise CalcError("une année est attendue")
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return ("oui" if leap else "non") if _lang(lang) == "fr" else ("yes" if leap else "no")


def _evens(values: Any, lang: Any = "fr") -> str | list[int | float]:
    evens = [v for v in _numbers(values) if v % 2 == 0]
    return evens or ("aucun" if _lang(lang) == "fr" else "no even numbers")


def _round(value: Any, digits: Any = 0) -> int | float:
    if not isinstance(value, int | float) or not isinstance(digits, int) or not 0 <= digits <= 12:
        raise CalcError("round(nombre, chiffres) attendu")
    return round(value, digits)


#: Fonctions pures (aucun effet de bord) que le modèle peut appeler.
FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sorted": lambda v: sorted(_numbers(v)),
    "sorted_desc": lambda v: sorted(_numbers(v), reverse=True),
    "max": lambda v: max(_numbers(v)) if v else _fail("liste vide"),
    "min": lambda v: min(_numbers(v)) if v else _fail("liste vide"),
    "sum": lambda v: sum(_numbers(v)),
    "len": lambda v: len(v) if isinstance(v, str | list) else _fail("un texte ou une liste est attendu"),
    "abs": lambda v: abs(v) if isinstance(v, int | float) else _fail("un nombre est attendu"),
    "round": _round,
    "evens": _evens,
    "upper": lambda s: _text(s).upper(),
    "lower": lambda s: _text(s).lower(),
    "reverse": lambda s: _text(s)[::-1],
    "spell": lambda s: "-".join(_text(s)),
    "first": lambda s: _text(s)[:1],
    "last": lambda s: _text(s)[-1:],
    "weekday": _weekday,
    "add_days": _add_days,
    "days_between": _days_between,
    "is_leap": _is_leap,
}


def _fail(message: str) -> Any:
    raise CalcError(message)


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float, str):
        return _check_size(node.value)
    if isinstance(node, ast.List | ast.Tuple):
        return _check_size([_evaluate(element) for element in node.elts])
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        operand = _evaluate(node.operand)
        if not isinstance(operand, int | float):
            raise CalcError("signe devant autre chose qu'un nombre")
        return _UNARY[type(node.op)](operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (left, right)):
            raise CalcError("les opérateurs s'appliquent à des nombres")
        if isinstance(node.op, ast.Pow) and (
            abs(right) > MAX_EXPONENT or (abs(left) > 1 and right < -MAX_EXPONENT)
        ):
            raise CalcError("exposant trop grand")
        try:
            return _check_size(_BINARY[type(node.op)](left, right))
        except ZeroDivisionError as exc:
            raise CalcError("division par zéro") from exc
        except OverflowError as exc:
            raise CalcError("nombre trop grand") from exc
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FUNCTIONS
        and not node.keywords
        and not any(isinstance(arg, ast.Starred) for arg in node.args)
    ):
        try:
            return _check_size(FUNCTIONS[node.func.id](*(_evaluate(arg) for arg in node.args)))
        except TypeError as exc:
            raise CalcError(f"arguments invalides pour {node.func.id}") from exc
    raise CalcError(
        "expression non autorisée (nombres, textes, listes, + - * / // % ** et fonctions : "
        + ", ".join(sorted(FUNCTIONS))
        + ")"
    )


def format_number(value: int | float) -> str:
    """Entier tel quel ; décimal à 12 chiffres significatifs, sans zéros inutiles (`2.5`, pas `2.50`)."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(format_value(item) for item in value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return format_number(value)
    return str(value)


def evaluate(expression: str) -> str:
    """Résultat exact de `expression`, en texte ; `CalcError` si l'expression est refusée."""
    # Signes typographiques acceptés : multiplication, division, moins.
    text = expression.strip().replace("\u00d7", "*").replace("\u00f7", "/").replace("\u2212", "-")
    if not text:
        raise CalcError("expression vide")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise CalcError("expression trop longue")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalcError("expression illisible") from exc
    return format_value(_evaluate(tree))


def run(expression: str) -> str:
    """Ce que le modèle lit après `<|tool_result|>` : le résultat, ou « erreur : … »."""
    try:
        return evaluate(expression)
    except CalcError as exc:
        return f"erreur : {exc}"
