"""Mesures objectives d'un rendu de Game Studio : un INSTRUMENT, jamais un juge.

Chaque valeur est calculée sur les pixels du rendu livré et, quand Game Studio le fournit, sur le masque de
silhouette de la même vue. Aucune ne dit si l'objet est beau. Elles décrivent ce que l'on voit (couleur
réellement perçue de la carrosserie, exposition, bruit, densité de détails), permettent de comparer deux
versions, et serviront à calibrer de futurs juges artistiques contre des avis humains.

Formules et seuils fixés ici, une fois pour toutes :

- couleur : sRGB vers CIELAB (D65) ; médiane des pixels du sujet entre les centiles 35 et 95 de L* (sans
  pneus, vitres ni reflets : la carrosserie probable) ; nom de teinte par l'angle h° et la chroma C* ;
- écart à la couleur demandée : ΔE CIE76 entre cette médiane et la couleur nommée de référence ;
- exposition : part des pixels à L* > 98 (hautes lumières brûlées) et L* < 2 (ombres bouchées) ;
- contraste : écart type de L* sur toute l'image ;
- reflets : part des pixels du sujet à L* > 85 (reflets francs, attendus sur une peinture vernie) ;
- séparation : écart de clarté entre la carrosserie et la médiane du fond ;
- bruit : écart type du laplacien de L* dans les zones lisses du fond ;
- détails : part des pixels du sujet dont le gradient de L* dépasse 12 (arêtes, joints, entrées d'air).

Dépend de NumPy et Pillow, présents avec Game Studio ; sans eux, `available()` est faux.
"""

from __future__ import annotations

import io
import math
from typing import Any

#: Couleurs nommées de référence (sRGB 8 bits) : une teinte franche, typique d'une carrosserie de cette
#: couleur. Une référence, pas une vérité : l'écart dit « loin de… », jamais « faux ».
REFERENCE_COLORS: dict[str, tuple[int, int, int]] = {
    "rouge": (190, 20, 25),
    "jaune": (240, 190, 20),
    "orange": (235, 110, 20),
    "vert": (30, 140, 60),
    "bleu": (20, 60, 170),
    "violet": (100, 40, 150),
    "rose": (230, 110, 160),
    "blanc": (238, 238, 236),
    "noir": (18, 18, 20),
    "gris": (120, 122, 125),
    "argent": (180, 182, 186),
}

HIGHLIGHT_L, SHADOW_L, SPECULAR_L, EDGE_GRADIENT = 98.0, 2.0, 85.0, 12.0


def available() -> bool:
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _lab(rgb: Any) -> Any:
    """sRGB 8 bits (hauteur, largeur, 3) vers CIELAB D65, formules standard."""
    import numpy as np

    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
                    axis=-1)


def lab_of(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    import numpy as np

    value = _lab(np.array([[rgb]], dtype=np.uint8))[0, 0]
    return float(value[0]), float(value[1]), float(value[2])


def color_name(lab: tuple[float, float, float]) -> str:
    """Nom français d'une couleur perçue, avec sa clarté et sa saturation (« saumon clair »)."""
    lightness, a, b = lab
    chroma = math.hypot(a, b)
    if chroma < 10:
        if lightness > 85:
            return "blanc"
        if lightness < 20:
            return "noir"
        return "gris clair" if lightness > 60 else "gris"
    hue = math.degrees(math.atan2(b, a)) % 360
    if hue < 20 or hue >= 345:
        base = "rose" if lightness > 65 and chroma < 45 else "rouge"
    elif hue < 45:
        base = "rouge orangé" if chroma > 45 else "saumon" if lightness > 55 else "brun rouge"
    elif hue < 70:
        base = "orange" if chroma > 40 else "beige" if lightness > 60 else "brun"
    elif hue < 105:
        base = "jaune" if lightness > 55 else "olive"
    elif hue < 165:
        base = "vert"
    elif hue < 230:
        base = "cyan" if lightness > 50 else "bleu pétrole"
    elif hue < 300:
        base = "bleu" if hue < 275 else "violet"
    else:
        base = "magenta" if chroma > 45 else "rose"
    light = " clair" if lightness > 70 else " foncé" if lightness < 30 else ""
    sat = ", peu saturé" if chroma < 30 else ", très saturé" if chroma > 70 else ""
    return f"{base}{light}{sat}"


def _decode(data: bytes) -> Any:
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGB"))


def measure(image: bytes, mask: bytes | None = None, *, requested: str | None = None) -> dict[str, Any]:
    """Mesures d'un rendu. `mask` : silhouette blanche sur fond noir, de même taille, qui délimite le
    sujet ; sans masque, seules les mesures de l'image entière sont rendues. `requested` : couleur
    demandée (clé de `REFERENCE_COLORS`)."""
    import numpy as np

    lab = _lab(_decode(image))
    lightness = lab[..., 0]
    height, width = lightness.shape
    out: dict[str, Any] = {
        "kind": "instrument_uncalibrated",
        "resolution": [int(width), int(height)],
        "highlights_clipped_pct": round(float((lightness > HIGHLIGHT_L).mean() * 100), 2),
        "shadows_crushed_pct": round(float((lightness < SHADOW_L).mean() * 100), 2),
        "contrast_l_std": round(float(lightness.std()), 2),
    }
    if mask is None:
        out["subject"] = None
        return out
    silhouette = _decode(mask).mean(axis=-1) > 127
    if silhouette.shape != lightness.shape:
        raise ValueError(f"masque {silhouette.shape[::-1]} et rendu {lightness.shape[::-1]} "
                         "de tailles différentes")
    if silhouette.sum() < 100:
        raise ValueError("silhouette presque vide")
    gy, gx = np.gradient(lightness)
    gradient = np.hypot(gx, gy)
    laplacian = (np.roll(lightness, 1, 0) + np.roll(lightness, -1, 0) + np.roll(lightness, 1, 1)
                 + np.roll(lightness, -1, 1) - 4 * lightness)[1:-1, 1:-1]
    pixels = lab[silhouette]
    # Pneus, vitres et garnitures sont les pixels les plus sombres du sujet, les reflets les plus clairs ;
    # la carrosserie occupe l'essentiel du reste.
    low, high = np.percentile(pixels[:, 0], [35, 95])
    body = pixels[(pixels[:, 0] >= low) & (pixels[:, 0] <= high)]
    median = tuple(float(v) for v in np.median(body if len(body) else pixels, axis=0))
    rows, cols = np.nonzero(silhouette)
    smooth = ~silhouette[1:-1, 1:-1] & (gradient[1:-1, 1:-1] < 2.0)
    subject = {
        "coverage_pct": round(float(silhouette.mean() * 100), 2),
        "margins_px": [int(cols.min()), int(rows.min()), int(width - 1 - cols.max()),
                       int(height - 1 - rows.max())],
        "perceived_lab": [round(v, 1) for v in median],
        "perceived_chroma": round(math.hypot(median[1], median[2]), 1),
        "perceived_color": color_name(median),
        "specular_pct": round(float((pixels[:, 0] > SPECULAR_L).mean() * 100), 2),
        "detail_edge_pct": round(float((gradient[silhouette] > EDGE_GRADIENT).mean() * 100), 2),
        "separation_delta_l": round(abs(median[0] - float(np.median(lightness[~silhouette]))), 1),
    }
    out["noise_background"] = round(float(laplacian[smooth].std()), 3) if smooth.sum() > 1000 else None
    if requested in REFERENCE_COLORS:
        subject["requested_color"] = requested
        subject["delta_e76_to_requested"] = round(math.dist(median, lab_of(REFERENCE_COLORS[requested])), 1)
    out["subject"] = subject
    return out
