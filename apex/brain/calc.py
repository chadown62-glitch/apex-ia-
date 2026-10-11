"""Calculatrice EXACTE du Cerveau APEX — l'outil que le modèle appelle au lieu de calculer « de tête ».

Le modèle écrit une expression (`974*89`) entre `<|tool_call|>` et `<|tool_result|>` ; le moteur
d'inférence l'évalue ici et place le résultat dans la réponse : le modèle décide QUOI calculer, le calcul
lui-même est exact.

Sûreté : l'expression est analysée (arbre syntaxique Python) et SEULS les nombres, les parenthèses et les
opérateurs `+ - * / // % **` sont acceptés — aucun nom, aucun appel, aucun attribut : rien ne s'exécute.
Bornes : longueur de l'expression, exposant, taille des nombres (un calcul démesuré est refusé, jamais
bloquant).
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

MAX_EXPRESSION_CHARS = 200
MAX_EXPONENT = 64
MAX_DIGITS = 60

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


class CalcError(ValueError):
    """Expression refusée ou incalculable — le message est montré au modèle à la place du résultat."""


def _check_size(value: int | float) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise CalcError("résultat infini")
    if isinstance(value, int) and len(str(abs(value))) > MAX_DIGITS:
        raise CalcError("nombre trop grand")
    return value


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return _check_size(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
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
    raise CalcError("expression non autorisée (nombres et + - * / // % ** seulement)")


def format_number(value: int | float) -> str:
    """Entier tel quel ; décimal à 12 chiffres significatifs, sans zéros inutiles (`2.5`, pas `2.50`)."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.12g}"
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
    return format_number(_evaluate(tree))


def run(expression: str) -> str:
    """Ce que le modèle lit après `<|tool_result|>` : le résultat, ou « erreur : … »."""
    try:
        return evaluate(expression)
    except CalcError as exc:
        return f"erreur : {exc}"
