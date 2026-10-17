"""Atelier 3D d'APEX — d'un APPEL (`vehicle("tank", length=7, color="kaki")`) à des fichiers prêts
pour Blender, Unity et Unreal.

Le Cerveau (ou un autre modèle, par l'outil `model3d.create` de l'assistant) décide QUOI construire ; ce
module le construit avec `apex.brain.mesh3d` (géométrie exacte, vérifiable) et rend :

- `run(expression)` : le résumé que le moteur d'inférence place dans la réponse (comme la calculatrice) —
  dimensions RÉELLES du modèle construit (une valeur hors bornes est ramenée dans les bornes, et c'est
  dit) ;
- `model_files(model, stem)` : les fichiers — glTF binaire (`.glb` : Blender, Unreal, Godot ; Unity avec
  glTFast), OBJ + MTL (Unity, tout logiciel 3D), script Blender (reconstruit le modèle, exporte en .glb
  ou .fbx), aperçu PNG, script Unity (C#), classe Unreal (C++), notice.

Sûreté : l'expression est analysée (arbre syntaxique) ; seuls sont acceptés les trois générateurs, des
arguments littéraux (nombres, textes, triplets de couleur) et les noms d'arguments de chaque générateur —
rien ne s'exécute.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Any

from apex.brain import mesh3d
from apex.brain.mesh3d import Model

MAX_EXPRESSION_CHARS = 300

#: Couleurs nommées (français et anglais) -> (nom français, RVB linéaire 0..1).
COLORS: dict[str, tuple[str, tuple[float, float, float]]] = {}
for _names, _rgb in (
    (("rouge", "red"), (0.70, 0.08, 0.06)),
    (("bleu", "blue"), (0.10, 0.22, 0.60)),
    (("vert", "green"), (0.12, 0.45, 0.15)),
    (("noir", "black"), (0.04, 0.04, 0.045)),
    (("blanc", "white"), (0.90, 0.90, 0.88)),
    (("gris", "grey", "gray"), (0.40, 0.41, 0.43)),
    (("jaune", "yellow"), (0.90, 0.72, 0.08)),
    (("orange",), (0.90, 0.40, 0.05)),
    (("kaki", "khaki"), (0.30, 0.36, 0.22)),
    (("sable", "sand"), (0.72, 0.62, 0.42)),
    (("violet", "purple"), (0.40, 0.15, 0.55)),
    (("rose", "pink"), (0.90, 0.45, 0.60)),
    (("marron", "brown"), (0.35, 0.20, 0.10)),
    (("or", "gold"), (0.83, 0.66, 0.22)),
    (("argent", "silver"), (0.75, 0.76, 0.78)),
):
    for _name in _names:
        COLORS[_name] = (_names[0], _rgb)

#: Nom français (affiché, et racine des noms de fichiers) de chaque sorte de modèle.
LABELS = {
    "car": "voiture",
    "truck": "camion",
    "tank": "char",
    "motorbike": "moto",
    "sword": "épée",
    "axe": "hache",
    "rifle": "fusil",
    "pistol": "pistolet",
    "humanoid": "personnage",
}
_STEMS = {"épée": "epee"}
#: Mots qui NOMMENT chaque sorte dans une demande (français, anglais) : l'ébauche n'est présentée comme la
#: réponse que si l'objet demandé est celui construit (`matches_request`). Mesuré : sur des demandes hors
#: répertoire, le Cerveau construit souvent une sorte sans rapport (docs/CERVEAU_APEX_V2.md §9 septies).
REQUEST_NAMES: dict[str, tuple[str, ...]] = {
    "car": ("voiture", "voitures", "auto", "autos", "automobile", "berline", "cabriolet", "coupé",
            "bagnole", "car", "cars", "sedan"),
    "truck": ("camion", "camions", "camionnette", "poids lourd", "pick-up", "pickup", "truck", "trucks",
              "lorry"),
    "tank": ("char", "chars", "tank", "tanks", "blindé"),
    "motorbike": ("moto", "motos", "motocyclette", "scooter", "motorbike", "motorcycle"),
    "sword": ("épée", "épées", "sabre", "glaive", "katana", "sword", "swords"),
    "axe": ("hache", "haches", "axe", "axes"),
    "rifle": ("fusil", "fusils", "carabine", "rifle", "rifles", "sniper"),
    "pistol": ("pistolet", "pistolets", "revolver", "pistol", "handgun"),
    "humanoid": ("personnage", "personnages", "humanoïde", "héros", "guerrier", "guerrière", "chevalier",
                 "soldat", "magicien", "sorcier", "character", "humanoid", "hero", "warrior", "knight",
                 "soldier", "wizard"),
}
_REQUEST_PATTERNS = {
    kind: re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w])")
    for kind, names in REQUEST_NAMES.items()}


def requested_kinds(text: str) -> set[str]:
    """Les sortes NOMMÉES dans une demande (« une moto noire » → {motorbike})."""
    lowered = text.replace("\u2019", "'").lower()
    return {kind for kind, pattern in _REQUEST_PATTERNS.items() if pattern.search(lowered)}


def matches_request(expression: str, request: str) -> bool:
    """L'ébauche construite par cet appel est-elle l'objet demandé ? Faux pour un appel illisible, pour
    une autre sorte que celle demandée, ou pour une demande qui ne nomme aucune sorte du répertoire."""
    try:
        kind = parse(expression).kind
    except Error3D:
        return False
    return kind in requested_kinds(request)
BUILDS = {"slim": "mince", "normal": "normal", "strong": "costaud"}
#: Arguments acceptés par générateur (en plus de la sorte, premier argument ou `kind=`).
ARGUMENTS = {
    "vehicle": ("length", "width", "height", "wheels", "color"),
    "weapon": ("length", "color"),
    "character": ("height", "build", "color"),
}
DEFAULT_KIND = {"vehicle": "car", "weapon": "sword", "character": "humanoid"}


class Error3D(ValueError):
    pass


@dataclass(frozen=True)
class Call3D:
    generator: str
    kind: str
    options: tuple[tuple[str, Any], ...] = ()

    def arguments(self) -> dict[str, Any]:
        """Les mêmes paramètres pour l'outil `model3d.create` de l'assistant."""
        return {"generator": self.generator, "kind": self.kind, **dict(self.options)}

    def expression(self) -> str:
        parts = [repr(self.kind).replace("'", '"')]
        parts += [f"{key}={_literal(value)}" for key, value in self.options]
        return f"{self.generator}({', '.join(parts)})"


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + value + '"'
    if isinstance(value, tuple):
        return "(" + ", ".join(_literal(v) for v in value) + ")"
    return f"{value:g}" if isinstance(value, float) else str(value)


def is_call(expression: str) -> bool:
    """L'expression vise-t-elle l'atelier 3D (plutôt que la calculatrice) ?"""
    return re.match(r"\s*(vehicle|weapon|character)\s*\(", expression) is not None


def _value(node: ast.AST, name: str) -> Any:
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError) as exc:
        raise Error3D(f"{name} : valeur littérale attendue") from exc
    if isinstance(value, bool):
        raise Error3D(f"{name} : nombre ou texte attendu")
    if isinstance(value, (int, float)) and not math.isfinite(value):
        raise Error3D(f"{name} : nombre fini attendu")
    return value


def check_options(generator: str, kind: str, options: dict[str, Any]) -> Call3D:
    """Valide sorte et options (types, noms, couleurs) ; rend l'appel normalisé (options triées)."""
    if generator not in ARGUMENTS:
        raise Error3D(f"générateur inconnu : {generator} (vehicle, weapon, character)")
    accepted = ARGUMENTS[generator]
    for key, value in options.items():
        if key not in accepted:
            raise Error3D(f"{generator} : argument inconnu « {key} » ({', '.join(accepted)})")
        if key == "color":
            if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                options[key] = value.lower()
            elif isinstance(value, str):
                if value.lower() not in COLORS:
                    raise Error3D(
                        f"couleur inconnue : {value} ({', '.join(sorted({v[0] for v in COLORS.values()}))})"
                    )
                options[key] = value.lower()
            elif not (
                isinstance(value, (tuple, list))
                and len(value) == 3
                and all(
                    isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 1 for c in value
                )
            ):
                raise Error3D(
                    "couleur : un nom (« rouge »), un code #RRGGBB ou trois composantes entre 0 et 1"
                )
            else:
                options[key] = tuple(float(c) for c in value)
        elif key == "build":
            if value not in BUILDS:
                raise Error3D(f"morphologie inconnue : {value} ({', '.join(BUILDS)})")
        elif key == "wheels":
            if not isinstance(value, int):
                raise Error3D("wheels : nombre entier attendu")
        elif not isinstance(value, (int, float)):
            raise Error3D(f"{key} : nombre attendu (mètres)")
    if not isinstance(kind, str):
        raise Error3D("sorte : texte attendu")
    return Call3D(generator, kind, tuple(sorted(options.items())))


def parse(expression: str) -> Call3D:
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise Error3D(f"appel trop long (> {MAX_EXPRESSION_CHARS} caractères)")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise Error3D(f"appel illisible : {exc.msg}") from exc
    call = tree.body
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in ARGUMENTS):
        raise Error3D("appel attendu : vehicle(…), weapon(…) ou character(…)")
    generator = call.func.id
    if len(call.args) > 1:
        raise Error3D(f"{generator} : un seul argument sans nom (la sorte)")
    kind = _value(call.args[0], "sorte") if call.args else DEFAULT_KIND[generator]
    options: dict[str, Any] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise Error3D("arguments dépliés (**) refusés")
        if keyword.arg == "kind":
            if call.args:
                raise Error3D("sorte donnée deux fois")
            kind = _value(keyword.value, "kind")
            continue
        if keyword.arg in options:
            raise Error3D(f"argument donné deux fois : {keyword.arg}")
        options[keyword.arg] = _value(keyword.value, keyword.arg)
    return check_options(generator, kind, options)


def _hex_linear(code: str) -> tuple[float, float, float]:
    """#RRGGBB (sRGB, comme dans un logiciel de dessin) -> composantes linéaires (glTF, Blender)."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return tuple(round(channel(int(code[i : i + 2], 16) / 255), 4) for i in (1, 3, 5))  # type: ignore[return-value]


def build(call: Call3D) -> Model:
    options = dict(call.options)
    color = options.get("color")
    if isinstance(color, str):
        options["color"] = _hex_linear(color) if color.startswith("#") else COLORS[color][1]
    try:
        return mesh3d.GENERATORS[call.generator](call.kind, **options)
    except ValueError as exc:
        raise Error3D(str(exc)) from exc


def _num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def summary(model: Model, call: Call3D) -> str:
    """Ce que le modèle lit (et l'utilisateur voit) : sorte, dimensions RÉELLES, pièces, triangles, os."""
    category, _, kind = model.kind.partition("/")
    p = model.parameters
    asked = dict(call.options)
    if category == "vehicle":
        unit = "galets" if kind == "tank" else "roues"
        dims = f"{_num(p['length'])} × {_num(p['width'])} × {_num(p['height'])} m, {p['wheels']} {unit}"  # noqa: RUF001
    elif category == "weapon":
        dims = f"{_num(p['length'])} m"
    else:
        dims = f"{_num(p['height'])} m, {BUILDS[p['build']]}"
    stats = model.stats()
    text = f"{LABELS[kind]} {dims} — {stats['parts']} pièces, {stats['triangles']} triangles"
    if stats["bones"]:
        text += f", squelette de {stats['bones']} os"
    color = asked.get("color")
    if isinstance(color, str):
        text += f", {color if color.startswith('#') else COLORS[color][0]}"
    clamped = [
        f"{key} ramené à {_num(p[key])}"
        for key, value in asked.items()
        if key in p and isinstance(value, (int, float)) and not math.isclose(float(value), float(p[key]))
    ]
    if clamped:
        text += " (" + ", ".join(clamped) + ")"
    return text


def run(expression: str) -> str:
    """Ce que le moteur place après `<|tool_result|>` : le résumé du modèle construit, ou « erreur : … »."""
    try:
        call = parse(expression)
        return summary(build(call), call)
    except Error3D as exc:
        return f"erreur : {exc}"


def stem_for(model: Model, taken: set[str]) -> str:
    """Nom de fichier libre : `char`, puis `char-2`, `char-3`…"""
    _, _, kind = model.kind.partition("/")
    label = LABELS[kind]
    base = _STEMS.get(label, label)
    stem, index = base, 2
    while stem in taken:
        stem, index = f"{base}-{index}", index + 1
    return stem


def model_files(model: Model, stem: str, *, preview: bool = True) -> list[tuple[str, bytes]]:
    """[(nom de fichier, contenu)] — tout ce qu'il faut pour Blender, Unity et Unreal."""
    from apex.brain.gamecode import unity_script, unreal_class

    obj, mtl = mesh3d.to_obj(model, mtl_name=f"{stem}.mtl")
    files = [
        (f"{stem}.glb", mesh3d.to_glb(model)),
        (f"{stem}.obj", obj.encode("utf-8")),
        (f"{stem}.mtl", mtl.encode("utf-8")),
        (f"{stem}_blender.py", mesh3d.to_blender_script(model).encode("utf-8")),
    ]
    if preview:
        try:
            from apex.brain.preview3d import render
        except ImportError:  # numpy absent : pas d'aperçu, le reste est complet
            pass
        else:
            files.append((f"{stem}.png", render(model, size=384)))
    unity_name, unity_code = unity_script(model, stem)
    files.append((unity_name, unity_code.encode("utf-8")))
    files += [(name, code.encode("utf-8")) for name, code in unreal_class(model, stem)]
    names = [name for name, _ in files]
    stats = model.stats()
    size = " × ".join(_num(v) for v in stats["size_m"])  # noqa: RUF001 — signe de multiplication affiché
    notice = f"""Modèle « {model.name} » ({model.kind}) généré par APEX — {stats["parts"]} pièces,
{stats["triangles"]} triangles, {stats["bones"]} os, dimensions {size} m (avant, côté, haut ; Z en haut).

Fichiers :
- {stem}.glb : glTF 2.0 (Blender : Fichier > Importer > glTF ; Unreal : glisser dans le Content
  Browser ; Unity : avec le paquet glTFast).
- {stem}.obj + {stem}.mtl : Unity (glisser dans Assets), et tout logiciel 3D.
- {stem}_blender.py : reconstruit le modèle dans Blender (onglet Scripting > Exécuter), ou en ligne
  de commande : blender -b --python {stem}_blender.py -- {stem}.fbx   (FBX pour Unity, squelette compris)
- {unity_name} : script Unity (C#) de départ.
- {names[-2]} / {names[-1]} : classe Unreal Engine 5 (C++) de départ.

Ce que les tests d'APEX vérifient pour ce générateur : chaque pièce est un volume fermé et orienté ; le
glTF passe le validateur officiel Khronos ; le script Blender, exécuté par Blender 4.0, reconstruit les
mêmes pièces, triangles et dimensions (et, pour un personnage, le squelette déplace bien les pièces). Les
scripts Unity et Unreal ne sont vérifiés que pour la syntaxe (ni Unity ni Unreal n'étaient disponibles) :
à essayer dans le moteur. Modèles « low-poly » de base, à détailler.
"""
    files.append((f"{stem}_LISEZ-MOI.txt", notice.encode("utf-8")))
    return files
