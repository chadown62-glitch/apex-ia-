"""Exercices d'instruction « modélisation 3D » : une demande en langage courant (français ou anglais) ->
l'appel de l'atelier 3D d'APEX (`apex.brain.studio3d`), noté comme un appel de calculatrice. Le Cerveau
apprend à TRADUIRE la demande en paramètres (sorte, dimensions en mètres, couleur, morphologie) ; la
géométrie, elle, est construite par l'atelier, exactement.

Vérification : le résumé que l'atelier rend pour l'appel ATTENDU (sorte, dimensions réelles, nombre de
pièces et de triangles) doit figurer dans la réponse du modèle — il n'y figure que si le modèle a appelé
l'atelier avec les bons paramètres (le résultat d'un appel n'est jamais appris, le moteur le fournit).

Généralisation mesurée sur deux familles d'évaluation : `model3d` (formulations vues, paramètres jamais vus
ensemble) et `model3d_heldout` (formulations JAMAIS montrées à l'entraînement).
"""

# Tirages pseudo-aléatoires reproductibles pour générer des données, jamais pour de la sécurité (S311).
# ruff: noqa: S311

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from apex.brain import studio3d
from apex.brain.chat import calc_markup
from apex.neural.tasks import SYSTEM_EN, SYSTEM_FR, TaskExample, _assistant_instructions

#: Noms courants de chaque sorte -> (générateur, sorte).
NAMES_FR = {
    ("vehicle", "car"): ("une voiture", "une auto", "une berline"),
    ("vehicle", "truck"): ("un camion", "un poids lourd"),
    ("vehicle", "tank"): ("un char", "un char d'assaut", "un tank"),
    ("vehicle", "motorbike"): ("une moto", "une motocyclette"),
    ("weapon", "sword"): ("une épée",),
    ("weapon", "axe"): ("une hache",),
    ("weapon", "rifle"): ("un fusil", "un fusil d'assaut"),
    ("weapon", "pistol"): ("un pistolet",),
    ("character", "humanoid"): ("un personnage", "un humanoïde", "un héros", "un guerrier"),
}
NAMES_EN = {
    ("vehicle", "car"): ("a car", "a sedan"),
    ("vehicle", "truck"): ("a truck", "a lorry"),
    ("vehicle", "tank"): ("a tank", "a battle tank"),
    ("vehicle", "motorbike"): ("a motorbike", "a motorcycle"),
    ("weapon", "sword"): ("a sword",),
    ("weapon", "axe"): ("an axe",),
    ("weapon", "rifle"): ("a rifle",),
    ("weapon", "pistol"): ("a pistol", "a handgun"),
    ("character", "humanoid"): ("a character", "a humanoid", "a hero", "a warrior"),
}
COLORS_FR = ("rouge", "bleu", "vert", "noir", "blanc", "gris", "jaune", "orange", "kaki", "sable", "violet")
COLORS_EN = {
    "red": "rouge",
    "blue": "bleu",
    "green": "vert",
    "black": "noir",
    "white": "blanc",
    "grey": "gris",
    "yellow": "jaune",
    "orange": "orange",
    "khaki": "kaki",
    "sand": "sable",
}
BUILDS_FR = {"slim": ("mince", "élancé"), "normal": ("normal",), "strong": ("costaud", "musclé")}
BUILDS_EN = {"slim": ("slim", "lean"), "normal": ("average",), "strong": ("strong", "muscular", "bulky")}

#: Plages réalistes (mètres) par sorte : longueur ou taille, largeur, hauteur.
RANGES = {
    "car": ((3.5, 5.5), (1.6, 2.1), (1.3, 1.8)),
    "truck": ((6.0, 12.0), (2.3, 2.6), (2.8, 4.0)),
    "tank": ((6.0, 10.0), (3.0, 4.0), (2.2, 3.2)),
    "motorbike": ((1.8, 2.5), (0.7, 0.9), (1.0, 1.4)),
    "sword": ((0.7, 1.5),),
    "axe": ((0.5, 1.2),),
    "rifle": ((0.8, 1.3),),
    "pistol": ((0.15, 0.3),),
    "humanoid": ((1.5, 2.2),),
}

# Formulations : {obj} (« un char »), {dims} (« de 7 m de long »), {color}, {build}. Les dernières de chaque
# liste ne sont JAMAIS montrées à l'entraînement (famille `model3d_heldout`).
TEMPLATES_FR = (
    "Modélise {obj}{dims}{color}.",
    "Crée un modèle 3D : {obj}{dims}{color}.",
    "Je veux {obj}{dims}{color} pour mon jeu.",
    "Fais-moi {obj}{color}{dims}, en 3D.",
    "Génère {obj}{dims}{color} pour Blender.",
    "Il me faut {obj}{dims}{color} à importer dans Unity.",
    "Construis {obj}{color}{dims} pour Unreal Engine.",
    "Peux-tu modéliser {obj}{dims}{color} ?",
    "Dessine-moi en 3D {obj}{dims}{color}.",
    "Prépare {obj}{dims}{color}, prêt pour un moteur de jeu.",
)
TEMPLATES_EN = (
    "Model {obj}{dims}{color}.",
    "Create a 3D model of {obj}{dims}{color}.",
    "I need {obj}{dims}{color} for my game.",
    "Make me {obj}{color}{dims} in 3D.",
    "Generate {obj}{dims}{color} for Blender.",
    "Build {obj}{dims}{color} I can import into Unity.",
    "Could you model {obj}{color}{dims}?",
    "Design {obj}{dims}{color} for Unreal Engine.",
)
HELDOUT_TEMPLATES = 2  # par langue : les deux dernières formulations

TAIL_FR = "\nModèle 3D construit (low-poly, prêt à détailler)."
TAIL_EN = "\n3D model built (low-poly base, ready to refine)."


def _fr_number(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _en_number(value: float) -> str:
    return f"{value:g}"


@dataclass(frozen=True)
class _Request:
    generator: str
    kind: str
    options: tuple[tuple[str, object], ...]


def _draw(rng: random.Random) -> _Request:
    generator, kind = rng.choice(list(NAMES_FR))
    ranges = RANGES[kind]
    options: list[tuple[str, object]] = []
    if generator == "character":
        if rng.random() < 0.8:
            options.append(("height", round(rng.uniform(*ranges[0]), 2)))
        if rng.random() < 0.6:
            options.append(("build", rng.choice(("slim", "normal", "strong"))))
    else:
        if rng.random() < 0.85:
            options.append(("length", round(rng.uniform(*ranges[0]), 1 if kind != "pistol" else 2)))
        if generator == "vehicle":
            if rng.random() < 0.35:
                options.append(("width", round(rng.uniform(*ranges[1]), 1)))
            if rng.random() < 0.35:
                options.append(("height", round(rng.uniform(*ranges[2]), 1)))
            if kind in ("car", "truck") and rng.random() < 0.25:
                options.append(("wheels", rng.choice((4, 6, 8) if kind == "truck" else (4, 6))))
    if rng.random() < 0.5:
        options.append(("color", rng.choice(COLORS_FR)))
    return _Request(generator, kind, tuple(options))


def _phrase(request: _Request, lang: str, rng: random.Random) -> tuple[str, str, str]:
    """(objet, dimensions, couleur) dans la langue demandée, avec les mêmes valeurs que l'appel."""
    options = dict(request.options)
    fr = lang == "fr"
    names = (NAMES_FR if fr else NAMES_EN)[(request.generator, request.kind)]
    obj = rng.choice(names)
    num = _fr_number if fr else _en_number
    parts: list[str] = []
    if "length" in options:
        value = num(float(options["length"]))  # type: ignore[arg-type]
        parts.append(
            rng.choice((f"de {value} m de long", f"de {value} mètres", f"long de {value} m"))
            if fr
            else rng.choice((f"{value} m long", f"{value} meters long"))
        )
    if "width" in options:
        value = num(float(options["width"]))  # type: ignore[arg-type]
        parts.append(f"large de {value} m" if fr else f"{value} m wide")
    if "height" in options:
        value = num(float(options["height"]))  # type: ignore[arg-type]
        if request.generator == "character":
            parts.append(rng.choice((f"de {value} m", f"mesurant {value} m")) if fr else f"{value} m tall")
        else:
            parts.append(f"haut de {value} m" if fr else f"{value} m high")
    if "wheels" in options:
        parts.append(f"à {options['wheels']} roues" if fr else f"with {options['wheels']} wheels")
    if "build" in options:
        words = (BUILDS_FR if fr else BUILDS_EN)[str(options["build"])]
        parts.insert(0, rng.choice(words))
    dims = ""
    if parts:
        joiner = ", " if rng.random() < 0.5 else (" et " if fr else " and ")
        dims = " " + (joiner.join(parts) if len(parts) <= 2 else ", ".join(parts))
    color = ""
    if "color" in options:
        name = str(options["color"])
        if fr:
            color = rng.choice((f" {name}", f" de couleur {name}", f", couleur {name}"))
        else:
            english = (
                next(k for k, v in COLORS_EN.items() if v == name) if name in COLORS_EN.values() else None
            )
            color = f" in {english}" if english else ""
    return obj, dims, color


def _call(request: _Request, lang: str) -> str:
    """L'appel ATTENDU : sorte en premier, options dans un ordre fixe ; une couleur que la demande anglaise
    ne
    nomme pas (pas de traduction) n'est pas passée."""
    options = dict(request.options)
    if lang == "en" and "color" in options and options["color"] not in COLORS_EN.values():
        options.pop("color")
    ordered = [k for k in ("length", "width", "height", "wheels", "build", "color") if k in options]
    args = [f'"{request.kind}"'] + [
        f'{k}="{options[k]}"' if isinstance(options[k], str) else f"{k}={options[k]:g}" for k in ordered
    ]
    return f"{request.generator}({', '.join(args)})"


def _heldout_combination(request: _Request) -> bool:
    """Combinaisons (sorte, couleur, présence de chaque dimension) réservées à l'évaluation (~15 %)."""
    signature = (
        f"{request.kind}|{sorted(k for k, _ in request.options)}|{dict(request.options).get('color')}"
    )
    return int(hashlib.sha256(signature.encode()).hexdigest(), 16) % 100 < 15


def _system(lang: str, rng: random.Random) -> str | None:
    roll = rng.random()
    if roll < 0.25:
        return SYSTEM_FR if lang == "fr" else SYSTEM_EN
    if roll < 0.4:
        return _assistant_instructions()
    if roll < 0.65:
        from apex.assistant.modes import MODES
        from apex.assistant.turns import system_prompt

        return system_prompt(
            MODES["3d"], project_name=None, project_instructions="", tools=MODES["3d"].tools
        )
    return None


def model3d_examples(
    count: int, *, seed: int, split: str = "train", exclude: frozenset[str] = frozenset()
) -> list[TaskExample]:
    """`split="train"` : formulations et combinaisons d'entraînement seulement, jamais une demande de
    `exclude`. `split="eval"` : `count` exemples par famille (`model3d` : formulations vues sur des
    combinaisons réservées ; `model3d_heldout` : formulations jamais vues)."""
    rng = random.Random(f"model3d-{seed}-{split}")
    examples: list[TaskExample] = []
    seen: set[str] = set()
    wanted = {"model3d": count, "model3d_heldout": count} if split == "eval" else {"model3d": count}
    made = dict.fromkeys(wanted, 0)
    attempts = 0
    while any(made[f] < wanted[f] for f in wanted) and attempts < count * 50:
        attempts += 1
        request = _draw(rng)
        lang = "fr" if rng.random() < 0.6 else "en"
        templates = TEMPLATES_FR if lang == "fr" else TEMPLATES_EN
        heldout_combo = _heldout_combination(request)
        if split == "train":
            if heldout_combo:
                continue
            family, template = "model3d", rng.choice(templates[:-HELDOUT_TEMPLATES])
        else:
            family = rng.choice([f for f in wanted if made[f] < wanted[f]])
            if family == "model3d" and not heldout_combo:
                continue  # formulations vues : seulement sur des combinaisons jamais montrées
            heldout = family == "model3d_heldout"
            template = rng.choice(
                templates[-HELDOUT_TEMPLATES:] if heldout else templates[:-HELDOUT_TEMPLATES]
            )
        obj, dims, color = _phrase(request, lang, rng)
        user = template.format(obj=obj, dims=dims, color=color)
        user = user[0].upper() + user[1:]
        if user in seen or user in exclude:
            continue
        seen.add(user)
        expression = _call(request, lang)
        result = studio3d.run(expression)
        if result.startswith("erreur"):
            raise AssertionError(f"appel de référence refusé : {expression} -> {result}")
        assistant = calc_markup(expression) + (TAIL_FR if lang == "fr" else TAIL_EN)
        example = TaskExample(
            family,
            lang,
            user,
            assistant,
            {"type": "model3d", "summary": result},
            system=_system(lang, rng),
            meta={"call": expression},
        )
        examples.append(example)
        made[family] += 1
    return examples
