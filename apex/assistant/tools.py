"""Outils DÉTERMINISTES des modes Finance, Design et 3D, enregistrés à côté des outils d'espace de travail
(`apex.tools.builtin`) dans le même moteur (`apex.tools.engine` : schéma validé, erreurs structurées, journal).
Aucun n'exécute de code arbitraire : `calc.evaluate` interprète un arbre syntaxique restreint (nombres,
opérateurs, quelques fonctions), jamais `eval` ; `model3d.create` construit un modèle paramétrique
(`apex.brain.studio3d`) et n'écrit que dans le dossier `modeles3d/` de l'espace de travail."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apex.tools.builtin import default_registry
from apex.tools.engine import ToolContext, ToolRegistry
from apex.tools.model import ErrorCode, Permission, ToolFailure, ToolSpec

_MAX_EXPRESSION = 500
_MAX_MAGNITUDE = 1e18
_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS: dict[str, Callable[..., Any]] = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log,
              "log10": math.log10, "exp": math.exp, "floor": math.floor, "ceil": math.ceil}
_CONSTANTS = {"pi": math.pi, "e": math.e}


def _number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolFailure(ErrorCode.INVALID_INPUT, "seuls les nombres sont acceptés")
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolFailure(ErrorCode.INVALID_INPUT, "résultat non fini (division par zéro ou dépassement)")
    if abs(value) > _MAX_MAGNITUDE:
        raise ToolFailure(ErrorCode.RESOURCE_LIMIT, "nombre trop grand (> 1e18)")
    return value


def evaluate(expression: str) -> float | int:
    """Évalue une expression arithmétique (nombres, + - * / // % **, parenthèses, round/abs/min/max/sqrt/log/
    log10/exp/floor/ceil, pi, e). Tout autre élément est refusé."""
    if len(expression) > _MAX_EXPRESSION:
        raise ToolFailure(ErrorCode.TOO_LARGE, f"expression trop longue (> {_MAX_EXPRESSION} caractères)")
    try:
        tree = ast.parse(expression.replace("^", "**").strip(), mode="eval")
    except SyntaxError as exc:
        raise ToolFailure(ErrorCode.INVALID_INPUT, f"expression illisible : {exc.msg}") from exc

    def walk(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            return _number(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or (abs(left) > 1e6 and abs(right) > 3)):
                raise ToolFailure(ErrorCode.RESOURCE_LIMIT, "puissance trop grande")
            try:
                return _number(_BINARY[type(node.op)](left, right))
            except ZeroDivisionError as exc:
                raise ToolFailure(ErrorCode.INVALID_INPUT, "division par zéro") from exc
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _number(_UNARY[type(node.op)](walk(node.operand)))
        if isinstance(node, ast.Name) and node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS \
                and not node.keywords and 0 < len(node.args) <= 10:
            args = [walk(arg) for arg in node.args]
            try:
                return _number(_FUNCTIONS[node.func.id](*args))
            except (ValueError, TypeError) as exc:
                raise ToolFailure(ErrorCode.INVALID_INPUT, f"{node.func.id} : {exc}") from exc
        raise ToolFailure(ErrorCode.INVALID_INPUT,
                          f"élément non autorisé dans le calcul : {type(node).__name__}")

    return walk(tree)


def _calc(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    result = evaluate(args["expression"])
    return {"expression": args["expression"], "result": round(result, 10) if isinstance(result, float) else result}


def projection(*, initial: float, monthly_contribution: float, annual_rate_percent: float, years: int,
               annual_inflation_percent: float = 0.0) -> dict[str, Any]:
    """Capitalisation MENSUELLE à taux annuel constant : taux mensuel = (1 + r)^(1/12) - 1, versement en fin
    de mois. Tableau par année ; valeurs réelles (pouvoir d'achat) si une inflation est donnée."""
    monthly_rate = (1 + annual_rate_percent / 100) ** (1 / 12) - 1
    balance, contributed = float(initial), float(initial)
    rows = []
    for year in range(1, years + 1):
        for _ in range(12):
            balance = balance * (1 + monthly_rate) + monthly_contribution
            contributed += monthly_contribution
        deflator = (1 + annual_inflation_percent / 100) ** year
        rows.append({"year": year, "contributed": round(contributed, 2), "balance": round(balance, 2),
                     "gains": round(balance - contributed, 2), "balance_real": round(balance / deflator, 2)})
    return {
        "formula": "solde(mois) = solde(mois - 1) × (1 + t) + versement ; t = (1 + taux annuel)^(1/12) − 1",  # noqa: RUF001 — symboles mathématiques affichés à l'utilisateur
        "monthly_rate_percent": round(monthly_rate * 100, 6),
        "rows": rows,
        "final": rows[-1],
        "assumptions": {"initial": initial, "monthly_contribution": monthly_contribution,
                        "annual_rate_percent": annual_rate_percent, "years": years,
                        "annual_inflation_percent": annual_inflation_percent},
    }


def _projection(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    return projection(initial=float(args.get("initial", 0)), monthly_contribution=float(args.get("monthly_contribution", 0)),
                      annual_rate_percent=float(args["annual_rate_percent"]), years=int(args["years"]),
                      annual_inflation_percent=float(args.get("annual_inflation_percent", 0)))


def loan(*, principal: float, annual_rate_percent: float, years: int) -> dict[str, Any]:
    """Prêt amortissable à mensualité constante : M = P × r / (1 − (1 + r)^−n), r = taux annuel / 12."""  # noqa: RUF002 — symboles mathématiques affichés à l'utilisateur
    n = years * 12
    r = annual_rate_percent / 100 / 12
    payment = principal / n if r == 0 else principal * r / (1 - (1 + r) ** -n)
    balance, rows = principal, []
    for year in range(1, years + 1):
        interest_year = 0.0
        for _ in range(12):
            interest = balance * r
            balance = max(balance - (payment - interest), 0.0)
            interest_year += interest
        rows.append({"year": year, "interest": round(interest_year, 2), "remaining": round(balance, 2)})
    total = payment * n
    return {"formula": "M = P × r / (1 − (1 + r)^−n), r = taux annuel / 12, n = mois",  # noqa: RUF001 — symboles mathématiques affichés à l'utilisateur
            "monthly_payment": round(payment, 2), "total_paid": round(total, 2),
            "total_interest": round(total - principal, 2), "rows": rows,
            "assumptions": {"principal": principal, "annual_rate_percent": annual_rate_percent, "years": years}}


def _loan(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    return loan(principal=float(args["principal"]), annual_rate_percent=float(args["annual_rate_percent"]),
                years=int(args["years"]))


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ToolFailure(ErrorCode.INVALID_INPUT, f"couleur « {color} » : format #RRGGBB ou #RGB attendu")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(color: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> dict[str, Any]:
    """Ratio de contraste WCAG 2.x : (L1 + 0,05) / (L2 + 0,05), L1 la plus claire."""
    l1, l2 = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    ratio = (l1 + 0.05) / (l2 + 0.05)
    return {"foreground": foreground, "background": background, "ratio": round(ratio, 2),
            "aa_normal_text": ratio >= 4.5, "aa_large_text": ratio >= 3.0,
            "aaa_normal_text": ratio >= 7.0, "aaa_large_text": ratio >= 4.5}


def _contrast(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    return contrast(args["foreground"], args["background"])


MODELS_DIR = "modeles3d"


def _model3d(args: dict[str, Any], ctx: ToolContext, spec: ToolSpec) -> dict[str, Any]:
    """Construit le modèle demandé et écrit ses fichiers (glTF, OBJ, script Blender, aperçu, scripts Unity et
    Unreal, notice) dans `modeles3d/` ; un nom libre est choisi (`char`, `char-2`…), rien n'est écrasé."""
    from apex.brain import studio3d

    generator = args["generator"]
    options = {k: v for k, v in args.items() if k not in ("generator", "kind")}
    try:
        call = studio3d.check_options(generator, args.get("kind") or studio3d.DEFAULT_KIND[generator], options)
        model = studio3d.build(call)
    except studio3d.Error3D as exc:
        raise ToolFailure(ErrorCode.INVALID_INPUT, str(exc)) from exc
    taken = {Path(path).stem for path, _size in ctx.workspace.files() if path.startswith(MODELS_DIR + "/")
             and path.endswith(".glb")}
    stem = studio3d.stem_for(model, taken)
    files = []
    for name, content in studio3d.model_files(model, stem):
        ctx.workspace.write_bytes(f"{MODELS_DIR}/{name}", content)
        files.append({"path": f"{MODELS_DIR}/{name}", "size": len(content)})
    preview = f"{MODELS_DIR}/{stem}.png"
    return {"summary": studio3d.summary(model, call), "call": call.expression(), "name": stem,
            "files": files, "preview": preview if any(f["path"] == preview for f in files) else None,
            "stats": model.stats()}


_COLOR = {"type": "string", "maxLength": 9}
_AMOUNT = {"type": "number", "minimum": 0, "maximum": 1e12}


def assistant_registry(*, problems: list[str] | None = None) -> ToolRegistry:
    """Outils d'espace de travail d'APEX + outils des modes Design et Finance (aucune permission requise :
    calcul pur, sans fichier ni exécution) + modèles 3D + délégation à Game Studio s'il est installé (sinon
    sa raison est ajoutée à `problems`)."""
    registry = default_registry()
    registry.register(ToolSpec(
        name="calc.evaluate",
        description="Calculer une expression arithmétique exacte (+ - * / % **, parenthèses, round, min, max, sqrt).",
        input_schema={"type": "object", "required": ["expression"],
                      "properties": {"expression": {"type": "string", "maxLength": _MAX_EXPRESSION}}},
        output_schema={"type": "object", "additionalProperties": True}, permissions=frozenset(),
    ), _calc)
    registry.register(ToolSpec(
        name="finance.projection",
        description=("Projeter une épargne : capital initial, versement mensuel, taux annuel (%), durée (années), "
                     "inflation annuelle (%) facultative. Tableau année par année."),
        input_schema={"type": "object", "required": ["annual_rate_percent", "years"], "properties": {
            "initial": _AMOUNT, "monthly_contribution": _AMOUNT,
            "annual_rate_percent": {"type": "number", "minimum": -50, "maximum": 100},
            "years": {"type": "integer", "minimum": 1, "maximum": 60},
            "annual_inflation_percent": {"type": "number", "minimum": -20, "maximum": 50}}},
        output_schema={"type": "object", "additionalProperties": True}, permissions=frozenset(),
    ), _projection)
    registry.register(ToolSpec(
        name="finance.loan",
        description="Prêt amortissable : capital, taux annuel (%), durée (années) -> mensualité, coût total, tableau.",
        input_schema={"type": "object", "required": ["principal", "annual_rate_percent", "years"], "properties": {
            "principal": {"type": "number", "minimum": 1, "maximum": 1e12},
            "annual_rate_percent": {"type": "number", "minimum": 0, "maximum": 100},
            "years": {"type": "integer", "minimum": 1, "maximum": 50}}},
        output_schema={"type": "object", "additionalProperties": True}, permissions=frozenset(),
    ), _loan)
    registry.register(ToolSpec(
        name="color.contrast",
        description="Ratio de contraste WCAG entre une couleur de texte et une couleur de fond (#RRGGBB).",
        input_schema={"type": "object", "required": ["foreground", "background"],
                      "properties": {"foreground": _COLOR, "background": _COLOR}},
        output_schema={"type": "object", "additionalProperties": True}, permissions=frozenset(),
    ), _contrast)
    registry.register(ToolSpec(
        name="model3d.create",
        description=("Construire un modèle 3D paramétrique (véhicule, arme, personnage) et écrire ses fichiers pour "
                     "Blender, Unity et Unreal dans modeles3d/ : .glb, .obj, script Blender, aperçu PNG, script "
                     "Unity C#, classe Unreal C++. Dimensions en mètres."),
        input_schema={"type": "object", "required": ["generator"], "properties": {
            "generator": {"type": "string", "enum": ["vehicle", "weapon", "character"]},
            "kind": {"type": "string", "enum": ["car", "truck", "tank", "motorbike", "sword", "axe", "rifle",
                                                "pistol", "humanoid"]},
            "length": {"type": "number", "minimum": 0.1, "maximum": 30},
            "width": {"type": "number", "minimum": 0.4, "maximum": 8},
            "height": {"type": "number", "minimum": 0.5, "maximum": 8},
            "wheels": {"type": "integer", "minimum": 2, "maximum": 16},
            "build": {"type": "string", "enum": ["slim", "normal", "strong"]},
            "color": {"type": "string", "maxLength": 9}}},
        output_schema={"type": "object", "additionalProperties": True},
        permissions=frozenset({Permission.WRITE_WORKSPACE}),
    ), _model3d)
    absent = register_game_studio(registry)
    if absent is not None and problems is not None:
        problems.append(absent)
    return registry


#: Outils de délégation de Game Studio (`apex.game_studio.delegation`, propriété de Codex) : lancer une
#: mission, suivre son état, lire ses résultats (empreintes, jamais les octets), l'annuler.
GAME_STUDIO_TOOLS = ("game_studio.create", "game_studio.status", "game_studio.results",
                     "game_studio.cancel")
_GAME_STUDIO_MODULES = ("apex.game_studio", "apex.game_studio.delegation")
GAME_STUDIO_ABSENT = "Game Studio ne fait pas partie de cette installation d'APEX"


def register_game_studio(registry: ToolRegistry) -> str | None:
    """Enregistre les outils de Game Studio quand ce module fait partie de l'installation. Sinon rien n'est
    offert et la raison est rendue (pour être dite) : module absent, ou présent mais impossible à charger
    (une dépendance manquante n'empêche jamais les autres outils de l'assistant). Ses outils et leurs
    contrôles (permissions, appartenance de la mission à l'espace de travail du tour) sont ceux de Game
    Studio : rien n'est réimplémenté ici."""
    try:
        # Module facultatif (absent d'une installation sans Game Studio) : non vérifié ici par mypy.
        from apex.game_studio.delegation import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
            register_game_studio_tools,
        )
    except ModuleNotFoundError as exc:
        if exc.name in _GAME_STUDIO_MODULES:
            return GAME_STUDIO_ABSENT
        return f"Game Studio n'a pas pu être chargé : {exc}"[:300]
    except ImportError as exc:
        return f"Game Studio n'a pas pu être chargé : {exc}"[:300]
    register_game_studio_tools(registry)
    return None
