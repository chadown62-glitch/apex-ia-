"""Modélisation 3D d'APEX — véhicules, armes et personnages PARAMÉTRIQUES, exportés pour Blender, Unity et Unreal.

Le Cerveau décide QUOI construire (« un char de 7 m avec une tourelle large ») ; ce module construit une géométrie
exacte et vérifiable, comme la calculatrice calcule : chaque pièce est un volume fermé (chaque arête partagée par
exactement deux triangles), orienté vers l'extérieur (volume signé positif), aux dimensions demandées.

- Formes : pavé (évasable), cylindre / tronc de cône / cône, sphère ; rotations et déplacements.
- Générateurs : `vehicle` (voiture, camion, char, moto), `weapon` (épée, hache, fusil, pistolet),
  `character` (humanoïde de base, avec squelette : os, positions, parents).
- Exports : glTF 2.0 binaire (`.glb` : importé par Blender, Unity, Unreal, Godot), OBJ + MTL, et un SCRIPT
  BLENDER (bpy) qui reconstruit le même modèle (matériaux, objets, squelette) — à coller dans l'onglet
  Scripting de Blender ou à lancer en ligne de commande.

Unités : mètres. Axes : Z vers le haut, X vers l'avant (convention de Blender) ; l'export glTF convertit en Y vers le
haut. Ce sont des modèles « low-poly » de base, propres et prêts à détailler, pas des sculptures.
"""

# Gabarits de géométrie et de script Blender, plus lisibles sur une ligne (E501).
# ruff: noqa: E501

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Material:
    name: str
    color: tuple[float, float, float]
    metallic: float = 0.0
    roughness: float = 0.6


@dataclass
class Part:
    name: str
    material: str
    vertices: list[Vec]
    faces: list[tuple[int, int, int]]

    def moved(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> Part:
        return Part(
            self.name, self.material, [(x + dx, y + dy, z + dz) for x, y, z in self.vertices], self.faces
        )

    def rotated(self, axis: str, degrees: float) -> Part:
        a = math.radians(degrees)
        c, s = math.cos(a), math.sin(a)

        def turn(p: Vec) -> Vec:
            x, y, z = p
            if axis == "x":
                return (x, c * y - s * z, s * y + c * z)
            if axis == "y":
                return (c * x + s * z, y, -s * x + c * z)
            return (c * x - s * y, s * x + c * y, z)

        return Part(self.name, self.material, [turn(v) for v in self.vertices], self.faces)


@dataclass(frozen=True)
class Bone:
    name: str
    head: Vec
    tail: Vec
    parent: str | None = None


@dataclass
class Model:
    name: str
    kind: str
    materials: dict[str, Material] = field(default_factory=dict)
    parts: list[Part] = field(default_factory=list)
    bones: list[Bone] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    #: Peau rigide : pièce -> os qui la porte (poids 1). Exportée en glTF (skin) et dans le script Blender
    #: (groupes de sommets + modificateur Armature) : poser le squelette déplace les pièces.
    skin: dict[str, str] = field(default_factory=dict)

    def material(
        self, name: str, color: tuple[float, float, float], metallic: float = 0.0, roughness: float = 0.6
    ) -> str:
        self.materials.setdefault(name, Material(name, color, metallic, roughness))
        return name

    def add(self, part: Part) -> Part:
        names = {p.name for p in self.parts}
        if part.name in names:
            index = 2
            while f"{part.name}_{index}" in names:
                index += 1
            part = Part(f"{part.name}_{index}", part.material, part.vertices, part.faces)
        self.parts.append(part)
        return part

    def bounds(self) -> tuple[Vec, Vec]:
        points = [v for part in self.parts for v in part.vertices]
        low = tuple(min(p[i] for p in points) for i in range(3))
        high = tuple(max(p[i] for p in points) for i in range(3))
        return low, high  # type: ignore[return-value]

    def size(self) -> Vec:
        low, high = self.bounds()
        return (high[0] - low[0], high[1] - low[1], high[2] - low[2])

    def stats(self) -> dict[str, Any]:
        size = self.size()
        return {
            "name": self.name,
            "kind": self.kind,
            "parts": len(self.parts),
            "vertices": sum(len(p.vertices) for p in self.parts),
            "triangles": sum(len(p.faces) for p in self.parts),
            "materials": len(self.materials),
            "bones": len(self.bones),
            "size_m": [round(v, 3) for v in size],
        }


# ------------------------------------------------------------------------------------------------- formes


def box(
    name: str,
    material: str,
    sx: float,
    sy: float,
    sz: float,
    *,
    top: tuple[float, float] = (1.0, 1.0),
    top_shift: float = 0.0,
) -> Part:
    """Pavé centré à l'origine ; `top` réduit le dessus (évasement), `top_shift` le décale vers l'avant (x)."""
    x, y, z = sx / 2, sy / 2, sz / 2
    tx, ty = x * top[0], y * top[1]
    vertices = [
        (-x, -y, -z),
        (x, -y, -z),
        (x, y, -z),
        (-x, y, -z),
        (-tx + top_shift, -ty, z),
        (tx + top_shift, -ty, z),
        (tx + top_shift, ty, z),
        (-tx + top_shift, ty, z),
    ]
    faces = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    return Part(name, material, vertices, faces)


def cylinder(
    name: str,
    material: str,
    radius: float,
    length: float,
    *,
    segments: int = 16,
    axis: str = "z",
    radius_top: float | None = None,
) -> Part:
    """Cylindre (ou tronc de cône, ou cône si `radius_top=0`) centré, couché selon `axis`."""
    top = radius if radius_top is None else radius_top
    h = length / 2
    vertices: list[Vec] = []
    faces: list[tuple[int, int, int]] = []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        vertices.append((radius * math.cos(a), radius * math.sin(a), -h))
    if top > 1e-9:
        for i in range(segments):
            a = 2 * math.pi * i / segments
            vertices.append((top * math.cos(a), top * math.sin(a), h))
        for i in range(segments):
            j = (i + 1) % segments
            faces += [(i, j, segments + j), (i, segments + j, segments + i)]
        top_center = len(vertices)
        vertices.append((0.0, 0.0, h))
        faces += [(top_center, segments + i, segments + (i + 1) % segments) for i in range(segments)]
    else:
        apex = len(vertices)
        vertices.append((0.0, 0.0, h))
        faces += [(i, (i + 1) % segments, apex) for i in range(segments)]
    bottom_center = len(vertices)
    vertices.append((0.0, 0.0, -h))
    faces += [(bottom_center, (i + 1) % segments, i) for i in range(segments)]
    part = Part(name, material, vertices, faces)
    if axis == "x":
        return part.rotated("y", 90)
    if axis == "y":
        return part.rotated("x", -90)
    return part


def sphere(
    name: str,
    material: str,
    radius: float,
    *,
    rings: int = 8,
    segments: int = 16,
    scale: Vec = (1.0, 1.0, 1.0),
) -> Part:
    sx, sy, sz = scale
    vertices: list[Vec] = [(0.0, 0.0, radius * sz)]
    for k in range(1, rings):
        theta = math.pi * k / rings
        z, r = radius * math.cos(theta), radius * math.sin(theta)
        for j in range(segments):
            phi = 2 * math.pi * j / segments
            vertices.append((r * math.cos(phi) * sx, r * math.sin(phi) * sy, z * sz))
    vertices.append((0.0, 0.0, -radius * sz))
    south = len(vertices) - 1

    def ring(k: int, j: int) -> int:
        return 1 + (k - 1) * segments + j % segments

    faces = [(0, ring(1, j), ring(1, j + 1)) for j in range(segments)]
    for k in range(1, rings - 1):
        for j in range(segments):
            faces += [
                (ring(k, j), ring(k + 1, j), ring(k + 1, j + 1)),
                (ring(k, j), ring(k + 1, j + 1), ring(k, j + 1)),
            ]
    faces += [(south, ring(rings - 1, j + 1), ring(rings - 1, j)) for j in range(segments)]
    return Part(name, material, vertices, faces)


# ---------------------------------------------------------------------------------------------- contrôles


def check_part(part: Part) -> list[str]:
    """Problèmes de géométrie d'une pièce : indices, triangles dégénérés, volume non fermé, orientation."""
    problems: list[str] = []
    count = len(part.vertices)
    if any(not 0 <= i < count for face in part.faces for i in face):
        return [f"{part.name} : indice de sommet hors limites"]
    edges: dict[tuple[int, int], int] = {}
    volume = 0.0
    for a, b, c in part.faces:
        pa, pb, pc = part.vertices[a], part.vertices[b], part.vertices[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        if math.sqrt(sum(n * n for n in normal)) < 1e-12:
            problems.append(f"{part.name} : triangle dégénéré {a, b, c}")
        volume += (
            pa[0] * (pb[1] * pc[2] - pb[2] * pc[1])
            - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0])
            + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0])
        ) / 6
        for edge in ((a, b), (b, c), (c, a)):
            edges[edge] = edges.get(edge, 0) + 1
    if any(n != 1 or edges.get((b, a), 0) != 1 for (a, b), n in edges.items()):
        problems.append(f"{part.name} : volume non fermé ou orientation incohérente")
    if volume <= 0:
        problems.append(f"{part.name} : normales vers l'intérieur (volume {volume:.4g})")
    return problems


def check(model: Model) -> list[str]:
    problems = [p for part in model.parts for p in check_part(part)]
    used = {part.material for part in model.parts}
    problems += [f"matériau inconnu : {m}" for m in sorted(used - set(model.materials))]
    names = [b.name for b in model.bones]
    problems += [
        f"os {b.name} : parent inconnu {b.parent}"
        for b in model.bones
        if b.parent and b.parent not in names
    ]
    return problems


# ------------------------------------------------------------------------------------------- générateurs


def _clamp(value: float | None, default: float, low: float, high: float) -> float:
    return default if value is None else max(low, min(high, float(value)))


def vehicle(
    kind: str = "car",
    *,
    length: float | None = None,
    width: float | None = None,
    height: float | None = None,
    wheels: int | None = None,
    color: tuple[float, float, float] | None = None,
) -> Model:
    """Véhicule : `car` (voiture), `truck` (camion), `tank` (char), `motorbike` (moto). Dimensions en mètres."""
    kinds = {
        "car": (4.3, 1.8, 1.45, 4),
        "truck": (7.5, 2.5, 3.2, 6),
        "tank": (7.0, 3.4, 2.4, 10),
        "motorbike": (2.1, 0.8, 1.15, 2),
    }
    if kind not in kinds:
        raise ValueError(f"véhicule inconnu : {kind} ({', '.join(kinds)})")
    dl, dw, dh, dwheels = kinds[kind]
    L, W, H = _clamp(length, dl, 1.0, 30.0), _clamp(width, dw, 0.4, 8.0), _clamp(height, dh, 0.6, 8.0)
    n = int(_clamp(wheels, dwheels, 2, 16))
    m = Model(f"{kind}", f"vehicle/{kind}", parameters={"length": L, "width": W, "height": H, "wheels": n})
    paint = m.material(
        "peinture",
        color
        or {
            "car": (0.70, 0.08, 0.06),
            "truck": (0.12, 0.25, 0.55),
            "tank": (0.30, 0.36, 0.22),
            "motorbike": (0.08, 0.08, 0.09),
        }[kind],
        metallic=0.4,
        roughness=0.4,
    )
    rubber = m.material("pneu", (0.03, 0.03, 0.03), roughness=0.9)
    glass = m.material("vitre", (0.10, 0.16, 0.22), metallic=0.2, roughness=0.1)
    metal = m.material("metal", (0.55, 0.55, 0.58), metallic=1.0, roughness=0.35)
    light = m.material("phare", (0.95, 0.92, 0.70), roughness=0.2)
    if kind == "motorbike":
        r = H * 0.28
        for x in (L / 2 - r, -L / 2 + r):
            m.add(cylinder("roue", rubber, r, W * 0.18, axis="y", segments=20).moved(x, 0, r))
        m.add(box("cadre", paint, L * 0.55, W * 0.28, H * 0.22, top=(0.8, 1.0)).moved(0.0, 0, r + H * 0.18))
        m.add(
            box("reservoir", paint, L * 0.22, W * 0.45, H * 0.16, top=(0.8, 0.8)).moved(
                L * 0.08, 0, r + H * 0.36
            )
        )
        m.add(box("selle", rubber, L * 0.26, W * 0.38, H * 0.08).moved(-L * 0.14, 0, r + H * 0.38))
        m.add(
            cylinder("fourche", metal, H * 0.03, H * 0.62, axis="z")
            .rotated("y", -25)
            .moved(L / 2 - r * 1.25, 0, r + H * 0.25)
        )
        m.add(
            cylinder("guidon", metal, H * 0.025, W * 0.9, axis="y").moved(
                L / 2 - r * 1.55, 0, H * 0.95 - H * 0.03
            )
        )
        m.add(box("phare", light, H * 0.06, W * 0.2, H * 0.08).moved(L / 2 - r * 1.2, 0, H * 0.78))
        return m
    if kind == "tank":
        track_w = W * 0.22
        hull_h = H * 0.42
        r = hull_h * 0.42
        for side in (-1, 1):
            m.add(
                box("chenille", rubber, L, track_w, hull_h * 0.9, top=(0.92, 1.0)).moved(
                    0, side * (W / 2 - track_w / 2), hull_h * 0.45
                )
            )
            per_side = max(1, n // 2)
            for i in range(per_side):
                x = -L / 2 + L * (i + 0.5) / per_side
                m.add(
                    cylinder("galet", metal, r * 0.8, track_w * 0.3, axis="y").moved(
                        x * 0.92, side * (W / 2 - track_w - track_w * 0.15), r * 0.9
                    )
                )
        m.add(
            box("caisse", paint, L * 0.96, W - 2 * track_w, hull_h, top=(0.88, 0.95)).moved(
                0, 0, hull_h * 0.6
            )
        )
        turret_h = H - hull_h * 1.1
        m.add(
            box("tourelle", paint, L * 0.42, W * 0.58, turret_h, top=(0.75, 0.8)).moved(
                -L * 0.05, 0, hull_h * 1.1 + turret_h / 2
            )
        )
        m.add(
            cylinder("canon", metal, turret_h * 0.12, L * 0.55, axis="x", segments=16).moved(
                L * 0.42, 0, hull_h * 1.1 + turret_h * 0.5
            )
        )
        m.add(
            cylinder("trappe", paint, W * 0.09, turret_h * 0.15, axis="z").moved(
                -L * 0.12, W * 0.1, H - turret_h * 0.05
            )
        )
        return m
    r = H * (0.24 if kind == "car" else 0.17)
    body_h = H * (0.42 if kind == "car" else 0.38)
    per_side = max(1, n // 2)
    axles = (
        [L / 2 - r * 1.6]
        if per_side == 1
        else [L / 2 - r * 1.6 - (L - r * 3.2) * i / (per_side - 1) for i in range(per_side)]
    )
    for x in axles:
        for side in (-1, 1):
            m.add(
                cylinder("roue", rubber, r, W * 0.13, axis="y", segments=20).moved(
                    x, side * (W / 2 - W * 0.065), r
                )
            )
            m.add(
                cylinder("jante", metal, r * 0.55, W * 0.135, axis="y", segments=12).moved(
                    x, side * (W / 2 - W * 0.065), r
                )
            )
    if kind == "car":
        m.add(
            box("carrosserie", paint, L, W * 0.86, body_h, top=(0.97, 0.98)).moved(
                0, 0, r * 0.75 + body_h / 2
            )
        )
        cabin_h = H - r * 0.75 - body_h
        m.add(
            box(
                "habitacle", glass, L * 0.48, W * 0.76, cabin_h, top=(0.72, 0.85), top_shift=-L * 0.03
            ).moved(-L * 0.04, 0, r * 0.75 + body_h + cabin_h / 2)
        )
    else:
        cab_l = L * 0.24
        m.add(box("chassis", metal, L, W * 0.8, H * 0.08).moved(0, 0, r * 1.4))
        m.add(
            box("cabine", paint, cab_l, W * 0.92, H * 0.55, top=(0.92, 0.95)).moved(
                L / 2 - cab_l / 2, 0, r * 1.45 + H * 0.3
            )
        )
        m.add(
            box("pare_brise", glass, cab_l * 0.06, W * 0.78, H * 0.2).moved(
                L / 2 - cab_l * 0.06, 0, r * 1.45 + H * 0.42
            )
        )
        cargo_l = L - cab_l - L * 0.03
        m.add(
            box("caisse", paint, cargo_l, W, H - r * 1.5 - H * 0.04).moved(
                -L / 2 + cargo_l / 2, 0, r * 1.5 + (H - r * 1.5 - H * 0.04) / 2 + H * 0.04
            )
        )
    for side in (-1, 1):
        m.add(
            box("phare", light, L * 0.02, W * 0.12, H * 0.07).moved(
                L / 2 + L * 0.005, side * W * 0.3, r * 1.2
            )
        )
    return m


def weapon(
    kind: str = "sword", *, length: float | None = None, color: tuple[float, float, float] | None = None
) -> Model:
    """Arme : `sword` (épée), `axe` (hache), `rifle` (fusil), `pistol` (pistolet). Longueur totale en mètres."""
    defaults = {"sword": 1.0, "axe": 0.8, "rifle": 1.0, "pistol": 0.2}
    if kind not in defaults:
        raise ValueError(f"arme inconnue : {kind} ({', '.join(defaults)})")
    L = _clamp(length, defaults[kind], 0.1, 3.0)
    m = Model(kind, f"weapon/{kind}", parameters={"length": L})
    steel = m.material("acier", color or (0.72, 0.74, 0.78), metallic=1.0, roughness=0.25)
    dark = m.material("metal_sombre", (0.12, 0.12, 0.13), metallic=0.8, roughness=0.45)
    wood = m.material("bois", (0.36, 0.20, 0.09), roughness=0.7)
    leather = m.material("cuir", (0.20, 0.11, 0.06), roughness=0.85)
    if kind == "sword":
        grip = L * 0.2
        m.add(cylinder("poignee", leather, L * 0.017, grip, segments=10).moved(0, 0, grip / 2))
        m.add(sphere("pommeau", steel, L * 0.03, rings=6, segments=10).moved(0, 0, -L * 0.02))
        m.add(box("garde", steel, L * 0.04, L * 0.24, L * 0.03).moved(0, 0, grip + L * 0.015))
        blade = L - grip - L * 0.06
        m.add(
            box("lame", steel, L * 0.012, L * 0.05, blade * 0.88).moved(
                0, 0, grip + L * 0.03 + blade * 0.44
            )
        )
        m.add(
            box("pointe", steel, L * 0.012, L * 0.05, blade * 0.12, top=(0.15, 0.02)).moved(
                0, 0, grip + L * 0.03 + blade * 0.94
            )
        )
    elif kind == "axe":
        m.add(cylinder("manche", wood, L * 0.022, L, segments=10).moved(0, 0, L / 2))
        # Fer : épais contre le manche, aminci et plus haut vers le tranchant (+y).
        reach = L * 0.2
        head = box("fer", steel, L * 0.012, L * 0.22, reach, top=(4.0, 0.45)).rotated("x", 90)
        m.add(head.moved(0, L * 0.022 + reach / 2, L * 0.86))
        m.add(box("talon", dark, L * 0.05, L * 0.06, L * 0.1).moved(0, -L * 0.03, L * 0.86))
    elif kind == "rifle":
        m.add(
            box("crosse", wood, L * 0.3, L * 0.045, L * 0.11, top=(1.0, 0.9)).moved(-L * 0.35, 0, -L * 0.02)
        )
        m.add(box("carcasse", dark, L * 0.3, L * 0.05, L * 0.08).moved(-L * 0.05, 0, L * 0.01))
        m.add(
            cylinder("canon", dark, L * 0.012, L * 0.45, axis="x", segments=12).moved(L * 0.32, 0, L * 0.03)
        )
        m.add(box("garde_main", wood, L * 0.22, L * 0.05, L * 0.05).moved(L * 0.2, 0, L * 0.005))
        m.add(
            box("chargeur", dark, L * 0.05, L * 0.035, L * 0.13)
            .rotated("y", 12)
            .moved(L * 0.0, 0, -L * 0.09)
        )
        m.add(
            box("poignee", wood, L * 0.04, L * 0.04, L * 0.1)
            .rotated("y", -18)
            .moved(-L * 0.13, 0, -L * 0.07)
        )
        m.add(
            cylinder("lunette", dark, L * 0.02, L * 0.2, axis="x", segments=12).moved(
                -L * 0.02, 0, L * 0.085
            )
        )
    else:
        m.add(box("culasse", dark, L * 0.9, L * 0.14, L * 0.16).moved(L * 0.05, 0, L * 0.18))
        m.add(
            cylinder("canon", steel, L * 0.035, L * 0.1, axis="x", segments=12).moved(L * 0.52, 0, L * 0.19)
        )
        m.add(box("carcasse", dark, L * 0.7, L * 0.13, L * 0.08).moved(L * 0.1, 0, L * 0.07))
        m.add(
            box("crosse", leather, L * 0.2, L * 0.13, L * 0.5, top=(1.0, 1.0))
            .rotated("y", -15)
            .moved(-L * 0.28, 0, -L * 0.15)
        )
        m.add(box("detente", steel, L * 0.03, L * 0.03, L * 0.08).moved(L * 0.0, 0, -L * 0.0))
    return m


def character(
    kind: str = "humanoid",
    *,
    height: float | None = None,
    build: str = "normal",
    color: tuple[float, float, float] | None = None,
) -> Model:
    """Personnage humanoïde de base (pose en T, proportions de huit têtes), avec son squelette.
    `build` : `slim` (mince), `normal`, `strong` (costaud)."""
    if kind != "humanoid":
        raise ValueError("personnage inconnu : humanoid seulement")
    builds = {"slim": 0.85, "normal": 1.0, "strong": 1.25}
    if build not in builds:
        raise ValueError(f"morphologie inconnue : {build} ({', '.join(builds)})")
    H = _clamp(height, 1.8, 0.5, 4.0)
    w = builds[build]
    u = H / 8  # une « tête »
    m = Model("personnage", f"character/{kind}", parameters={"height": H, "build": build})
    skin = m.material("peau", color or (0.80, 0.62, 0.50), roughness=0.7)
    cloth = m.material("tenue", (0.18, 0.22, 0.30), roughness=0.8)
    boots = m.material("bottes", (0.10, 0.07, 0.05), roughness=0.8)
    hip_z, chest_z, neck_z = 4 * u, 6.0 * u, 7.0 * u
    m.add(
        sphere("tete", skin, u * 0.48, rings=8, segments=14, scale=(1.0, 0.85, 1.05)).moved(
            0, 0, H - u * 0.5
        )
    )
    m.add(cylinder("cou", skin, u * 0.17 * w, u * 0.4, segments=10).moved(0, 0, neck_z + u * 0.15))
    m.add(
        box("torse", cloth, u * 0.9 * w, u * 1.9 * w, chest_z + u * 0.9 - hip_z, top=(0.9, 1.0)).moved(
            0, 0, (hip_z + chest_z + u * 0.9) / 2
        )
    )
    m.add(box("bassin", cloth, u * 0.85 * w, u * 1.6 * w, u * 0.7).moved(0, 0, hip_z - u * 0.2))
    shoulder_y = u * 0.95 * w
    arm_r = u * 0.16 * w
    for side, tag in ((1, "G"), (-1, "D")):
        y0 = side * shoulder_y
        m.add(
            cylinder(f"bras_{tag}", cloth, arm_r, u * 1.5, axis="y", segments=10).moved(
                0, y0 + side * u * 0.75, chest_z + u * 0.6
            )
        )
        m.add(
            cylinder(f"avant_bras_{tag}", skin, arm_r * 0.85, u * 1.3, axis="y", segments=10).moved(
                0, y0 + side * u * 2.15, chest_z + u * 0.6
            )
        )
        m.add(
            box(f"main_{tag}", skin, u * 0.25, u * 0.5, u * 0.12).moved(
                0, y0 + side * u * 3.05, chest_z + u * 0.6
            )
        )
        y_leg = side * u * 0.45 * w
        m.add(
            cylinder(
                f"cuisse_{tag}", cloth, u * 0.26 * w, u * 1.9, segments=10, radius_top=u * 0.3 * w
            ).moved(0, y_leg, hip_z - u * 0.45 - u * 0.95)
        )
        m.add(
            cylinder(
                f"jambe_{tag}", boots, u * 0.17 * w, u * 1.75, segments=10, radius_top=u * 0.22 * w
            ).moved(0, y_leg, u * 0.25 + u * 0.875)
        )
        m.add(box(f"pied_{tag}", boots, u * 0.9, u * 0.35, u * 0.25).moved(u * 0.25, y_leg, u * 0.125))
    shoulder_z = chest_z + u * 0.6
    bones = [
        Bone("racine", (0, 0, 0), (0, 0, hip_z * 0.5)),
        Bone("bassin", (0, 0, hip_z - u * 0.3), (0, 0, hip_z + u * 0.3), "racine"),
        Bone("colonne", (0, 0, hip_z + u * 0.3), (0, 0, chest_z), "bassin"),
        Bone("poitrine", (0, 0, chest_z), (0, 0, neck_z), "colonne"),
        Bone("cou", (0, 0, neck_z), (0, 0, neck_z + u * 0.4), "poitrine"),
        Bone("tete", (0, 0, neck_z + u * 0.4), (0, 0, H), "cou"),
    ]
    for side, tag in ((1, "G"), (-1, "D")):
        y0, y_leg = side * shoulder_y, side * u * 0.45 * w
        bones += [
            Bone(f"epaule_{tag}", (0, side * u * 0.2, shoulder_z), (0, y0, shoulder_z), "poitrine"),
            Bone(f"bras_{tag}", (0, y0, shoulder_z), (0, y0 + side * u * 1.5, shoulder_z), f"epaule_{tag}"),
            Bone(
                f"avant_bras_{tag}",
                (0, y0 + side * u * 1.5, shoulder_z),
                (0, y0 + side * u * 2.8, shoulder_z),
                f"bras_{tag}",
            ),
            Bone(
                f"main_{tag}",
                (0, y0 + side * u * 2.8, shoulder_z),
                (0, y0 + side * u * 3.3, shoulder_z),
                f"avant_bras_{tag}",
            ),
            Bone(f"cuisse_{tag}", (0, y_leg, hip_z - u * 0.3), (0, y_leg, u * 2.0), "bassin"),
            Bone(f"jambe_{tag}", (0, y_leg, u * 2.0), (0, y_leg, u * 0.25), f"cuisse_{tag}"),
            Bone(f"pied_{tag}", (0, y_leg, u * 0.25), (u * 0.7, y_leg, 0.0), f"jambe_{tag}"),
        ]
    m.bones = bones
    # Chaque pièce suit l'os du même nom ; le torse suit la colonne.
    names = {b.name for b in bones}
    m.skin = {p.name: (p.name if p.name in names else "colonne") for p in m.parts}
    return m


GENERATORS: dict[str, Callable[..., Model]] = {"vehicle": vehicle, "weapon": weapon, "character": character}


# ------------------------------------------------------------------------------------------------- exports


def _gltf_vec(v: Vec) -> Vec:
    """Z vers le haut (Blender) -> Y vers le haut (glTF)."""
    return (v[0], v[2], -v[1])


def to_glb(model: Model) -> bytes:
    """glTF 2.0 binaire, un nœud nommé par pièce (sous un nœud racine), normales plates, matériaux PBR.
    Avec un squelette et une peau : un nœud par os (hiérarchie, positions de repos) et un `skin` ; chaque
    pièce est liée à son os (poids 1) — Blender, Unity et Unreal l'importent comme un maillage animable."""
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    material_names = list(model.materials)
    joints = [b.name for b in model.bones] if model.skin else []

    def blob(data: bytes, target: int | None) -> int:
        while len(binary) % 4:
            binary.append(0)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        binary.extend(data)
        buffer_views.append(view)
        return len(buffer_views) - 1

    for part in model.parts:
        positions: list[Vec] = []
        normals: list[Vec] = []
        for a, b, c in part.faces:
            pa, pb, pc = (_gltf_vec(part.vertices[i]) for i in (a, b, c))
            u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
            v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            size = math.sqrt(sum(x * x for x in n)) or 1.0
            n = (n[0] / size, n[1] / size, n[2] / size)
            positions += [pa, pb, pc]
            normals += [n, n, n]
        attributes: dict[str, int] = {}
        for values, kind in ((positions, "POSITION"), (normals, "NORMAL")):
            view = blob(b"".join(struct.pack("<3f", *p) for p in values), 34962)
            accessor: dict[str, Any] = {
                "bufferView": view,
                "componentType": 5126,
                "count": len(values),
                "type": "VEC3",
            }
            if kind == "POSITION":
                accessor["min"] = [min(p[i] for p in values) for i in range(3)]
                accessor["max"] = [max(p[i] for p in values) for i in range(3)]
            accessors.append(accessor)
            attributes[kind] = len(accessors) - 1
        if joints:
            joint = joints.index(model.skin[part.name])
            count = len(positions)
            accessors.append(
                {
                    "bufferView": blob(struct.pack("<4H", joint, 0, 0, 0) * count, 34962),
                    "componentType": 5123,
                    "count": count,
                    "type": "VEC4",
                }
            )
            attributes["JOINTS_0"] = len(accessors) - 1
            accessors.append(
                {
                    "bufferView": blob(struct.pack("<4f", 1.0, 0.0, 0.0, 0.0) * count, 34962),
                    "componentType": 5126,
                    "count": count,
                    "type": "VEC4",
                }
            )
            attributes["WEIGHTS_0"] = len(accessors) - 1
        meshes.append(
            {
                "name": part.name,
                "primitives": [{"attributes": attributes, "material": material_names.index(part.material)}],
            }
        )
    nodes: list[dict[str, Any]] = [{"name": part.name, "mesh": i} for i, part in enumerate(model.parts)]
    children = list(range(len(model.parts)))
    scene_roots: list[int] = []
    skins: list[dict[str, Any]] = []
    if joints:
        first = len(nodes)
        heads = {b.name: _gltf_vec(b.head) for b in model.bones}
        for bone in model.bones:
            head = heads[bone.name]
            origin = heads[bone.parent] if bone.parent else (0.0, 0.0, 0.0)
            node: dict[str, Any] = {
                "name": bone.name,
                "translation": [head[i] - origin[i] for i in range(3)],
            }
            kids = [first + j for j, b in enumerate(model.bones) if b.parent == bone.name]
            if kids:
                node["children"] = kids
            nodes.append(node)
        roots = [first + j for j, b in enumerate(model.bones) if not b.parent]
        # Un maillage avec peau est une racine de la scène (glTF : la position de son nœud est ignorée) ;
        # le nœud du modèle porte le squelette.
        scene_roots, children = children, roots
        for i in range(len(model.parts)):
            nodes[i]["skin"] = 0
        # Matrices inverses de liaison (colonne par colonne) : os sans rotation, translation opposée.
        inverse = b"".join(
            struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -h[0], -h[1], -h[2], 1)
            for h in (heads[name] for name in joints)
        )
        accessors.append(
            {"bufferView": blob(inverse, None), "componentType": 5126, "count": len(joints), "type": "MAT4"}
        )
        skins.append(
            {
                "name": f"{model.name}_squelette",
                "joints": list(range(first, first + len(joints))),
                "skeleton": roots[0],
                "inverseBindMatrices": len(accessors) - 1,
            }
        )
    nodes.append({"name": model.name, "children": children})
    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "APEX mesh3d"},
        "scene": 0,
        "scenes": [{"name": model.name, "nodes": [len(nodes) - 1, *scene_roots]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": [
            {
                "name": m.name,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [*m.color, 1.0],
                    "metallicFactor": m.metallic,
                    "roughnessFactor": m.roughness,
                },
            }
            for m in model.materials.values()
        ],
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
        "extras": {"apex": model.stats(), "parameters": model.parameters},
    }
    if skins:
        document["skins"] = skins
    chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    chunk += b" " * (-len(chunk) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(chunk) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(chunk), b"JSON")
        + chunk
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + bytes(binary)
    )


def to_obj(model: Model, mtl_name: str | None = None) -> tuple[str, str]:
    """(fichier .obj, fichier .mtl) ; Y vers le haut, la convention des fichiers OBJ (Blender, Unity et Unreal
    l'importent debout). `mtl_name` : nom du fichier .mtl référencé (défaut : `<nom du modèle>.mtl`)."""
    lines = [f"# APEX mesh3d — {model.name}", f"mtllib {mtl_name or model.name + '.mtl'}"]
    base = 1
    for part in model.parts:
        lines += [f"o {part.name}", f"usemtl {part.material}"]
        lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in map(_gltf_vec, part.vertices)]
        lines += [f"f {a + base} {b + base} {c + base}" for a, b, c in part.faces]
        base += len(part.vertices)
    mtl = []
    for m in model.materials.values():
        mtl += [
            f"newmtl {m.name}",
            f"Kd {m.color[0]:.4f} {m.color[1]:.4f} {m.color[2]:.4f}",
            f"Pm {m.metallic:.3f}",
            f"Pr {m.roughness:.3f}",
            "",
        ]
    return "\n".join(lines) + "\n", "\n".join(mtl)


#: Construction dans Blender (bpy), commune au script autonome et à l'extension « APEX Atelier 3D » : objets
#: tenus par RÉFÉRENCE (jamais retrouvés par leur nom : une scène peut déjà contenir une « roue »).
BLENDER_BUILD = """
def build_scene(DATA, collection=None):
    \"\"\"Construit le modèle décrit par DATA ; rend (objet racine, squelette ou None).\"\"\"
    collection = collection or bpy.context.scene.collection
    root = bpy.data.objects.new(DATA["name"], None)
    collection.objects.link(root)
    materials = {}
    for spec in DATA["materials"]:
        material = bpy.data.materials.new(spec["name"])
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        if shader is not None:
            shader.inputs["Base Color"].default_value = (*spec["color"], 1.0)
            shader.inputs["Metallic"].default_value = spec["metallic"]
            shader.inputs["Roughness"].default_value = spec["roughness"]
        material.diffuse_color = (*spec["color"], 1.0)
        materials[spec["name"]] = material
    parts = {}
    for spec in DATA["parts"]:
        mesh = bpy.data.meshes.new(spec["name"])
        mesh.from_pydata([tuple(v) for v in spec["vertices"]], [], [tuple(f) for f in spec["faces"]])
        mesh.validate()
        mesh.update()
        mesh.materials.append(materials[spec["material"]])
        obj = bpy.data.objects.new(spec["name"], mesh)
        obj.parent = root
        collection.objects.link(obj)
        parts[spec["name"]] = obj
    rig = None
    if DATA["bones"]:
        armature = bpy.data.armatures.new(DATA["name"] + "_squelette")
        rig = bpy.data.objects.new(DATA["name"] + "_squelette", armature)
        rig.parent = root
        collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        for spec in DATA["bones"]:
            bone = armature.edit_bones.new(spec["name"])
            bone.head, bone.tail = spec["head"], spec["tail"]
        for spec in DATA["bones"]:
            if spec["parent"]:
                armature.edit_bones[spec["name"]].parent = armature.edit_bones[spec["parent"]]
        bpy.ops.object.mode_set(mode="OBJECT")
    # Peau rigide : chaque pièce suit son os (poids 1) ; poser le squelette déplace les pièces.
    for part_name, bone_name in DATA["skin"].items():
        obj = parts[part_name]
        group = obj.vertex_groups.new(name=bone_name)
        group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        modifier = obj.modifiers.new("Armature", "ARMATURE")
        modifier.object = rig
    return root, rig
"""


def blender_data(model: Model) -> dict[str, Any]:
    """Description du modèle lue par `build_scene` (script Blender, extension)."""
    return {
        "name": model.name,
        "materials": [
            {"name": m.name, "color": list(m.color), "metallic": m.metallic, "roughness": m.roughness}
            for m in model.materials.values()
        ],
        "parts": [
            {
                "name": p.name,
                "material": p.material,
                "vertices": [[round(c, 6) for c in v] for v in p.vertices],
                "faces": [list(f) for f in p.faces],
            }
            for p in model.parts
        ],
        "bones": [
            {"name": b.name, "head": list(b.head), "tail": list(b.tail), "parent": b.parent}
            for b in model.bones
        ],
        "skin": model.skin,
    }


def to_blender_script(model: Model) -> str:
    """Script Blender (bpy) qui reconstruit le modèle : matériaux, un objet par pièce sous un objet racine,
    squelette (Armature) pour un personnage, chaque pièce liée à son os (groupe de sommets + modificateur
    Armature). Lancement : onglet Scripting, ou `blender -b --python modele.py -- sortie.glb` (ou `.fbx`,
    pour Unity) : exporte alors le résultat."""
    data = blender_data(model)
    return f'''"""Modèle « {model.name} » généré par APEX (mesh3d) — {model.stats()["parts"]} pièces, {model.stats()["triangles"]} triangles.
Blender : onglet Scripting > Ouvrir > Exécuter. Ligne de commande : blender -b --python ce_fichier.py -- sortie.glb
(ou sortie.fbx pour Unity)."""
import json
import sys

import bpy

DATA = json.loads({json.dumps(data, ensure_ascii=False, separators=(",", ":"))!r})
{BLENDER_BUILD}

root, rig = build_scene(DATA)
# Relevé fait par Blender lui-même : pièces, triangles, dimensions (comparés au modèle d'APEX).
parts = [o for o in root.children if o.type == "MESH"]
corners = [o.matrix_world @ v.co for o in parts for v in o.data.vertices]
stats = {{
    "parts": len(parts),
    "triangles": sum(len(p.vertices) - 2 for o in parts for p in o.data.polygons),
    "size_m": [round(max(c[i] for c in corners) - min(c[i] for c in corners), 3) for i in range(3)],
    "bones": len(rig.data.bones) if rig is not None else 0,
}}
print("APEX_BLENDER_STATS " + json.dumps(stats))
if "--" in sys.argv:
    output = sys.argv[sys.argv.index("--") + 1]
    # Seul le modèle est exporté (jamais le reste de la scène, comme le cube de départ de Blender).
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == root or obj in root.children_recursive)
    if output.lower().endswith(".fbx"):
        bpy.ops.export_scene.fbx(filepath=output, use_selection=True, add_leaf_bones=False)
    else:
        bpy.ops.export_scene.gltf(filepath=output, export_format="GLB", use_selection=True)
'''
