"""Aperçu PNG d'un modèle d'APEX (`apex.brain.mesh3d`) — rendu logiciel, sans Blender ni carte graphique.

Vue de trois quarts, ombrage plat (lumière directionnelle + ambiante), tampon de profondeur, fond dégradé.
Sert à MONTRER le résultat dans la conversation ; ce n'est pas un moteur de rendu réaliste.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np

from apex.brain.mesh3d import Model


def _png(pixels: np.ndarray) -> bytes:
    height, width, _ = pixels.shape
    raw = b"".join(b"\0" + pixels[row].astype(np.uint8).tobytes() for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def render(model: Model, *, size: int = 512, azimuth: float = 35.0, elevation: float = 22.0) -> bytes:
    """PNG carré de `size` pixels de côté, modèle entier cadré automatiquement."""
    triangles = np.array(
        [[part.vertices[i] for i in face] for part in model.parts for face in part.faces], dtype=float
    )
    colors = np.array(
        [model.materials[part.material].color for part in model.parts for _ in part.faces], dtype=float
    )
    low, high = triangles.reshape(-1, 3).min(0), triangles.reshape(-1, 3).max(0)
    center, radius = (low + high) / 2, max(float(np.linalg.norm(high - low)) / 2, 1e-6)
    a, e = math.radians(azimuth), math.radians(elevation)
    forward = np.array(
        [-math.cos(e) * math.cos(a), -math.cos(e) * math.sin(a), -math.sin(e)]
    )  # caméra -> modèle
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    points = triangles - center
    sx, sy, depth = points @ right, points @ up, points @ forward
    scale = size * 0.45 / radius
    px, py = size / 2 + sx * scale, size / 2 - sy * scale
    edges1, edges2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    normals = np.cross(edges1, edges2)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    light = np.array([0.4, -0.3, 0.85])
    light /= np.linalg.norm(light)
    shade = 0.35 + 0.65 * np.clip(normals @ light, 0, 1)
    facing = normals @ forward < 0  # faces tournées vers la caméra
    shaded = np.clip(colors * shade[:, None] * 255, 0, 255)
    gradient = np.linspace(232, 196, size)[:, None, None] * np.ones((1, size, 3))
    image = gradient * np.array([0.93, 0.95, 1.0])
    zbuffer = np.full((size, size), np.inf)
    for t in np.nonzero(facing)[0]:
        x0, x1, x2, y0, y1, y2 = px[t, 0], px[t, 1], px[t, 2], py[t, 0], py[t, 1], py[t, 2]
        xmin, xmax = max(int(min(x0, x1, x2)), 0), min(int(max(x0, x1, x2)) + 1, size)
        ymin, ymax = max(int(min(y0, y1, y2)), 0), min(int(max(y0, y1, y2)) + 1, size)
        if xmin >= xmax or ymin >= ymax:
            continue
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(xmin, xmax) + 0.5, np.arange(ymin, ymax) + 0.5)
        w0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
        w1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        z = w0 * depth[t, 0] + w1 * depth[t, 1] + w2 * depth[t, 2]
        window = zbuffer[ymin:ymax, xmin:xmax]
        nearer = inside & (z < window)
        window[nearer] = z[nearer]
        image[ymin:ymax, xmin:xmax][nearer] = shaded[t]
    return _png(image)
