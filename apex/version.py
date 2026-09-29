"""Version et informations de build.

Principe 13 : tout composant important porte une version. Celle du paquet est
la racine de toutes les empreintes d'expérience (phase 5) : sans elle, deux
résultats ne sont pas comparables.
"""

from __future__ import annotations

import platform
import sys

__version__ = "0.1.2"

#: Version de l'API HTTP (préfixe des routes).
API_VERSION = "v1"
#: Contrat entre le Panel Windows (`APEX.exe`) et le backend : forme de `GET /api/v1/status` et de
#: l'enregistrement de découverte. Incrémenté à toute modification incompatible ; le Panel refuse un
#: protocole qu'il ne connaît pas (VERSION_MISMATCH) au lieu de mal l'interpréter.
PANEL_PROTOCOL = 1

#: Version minimale de Python. Source unique : elle etait dupliquee entre
#: le doctor et le Gate, ce qui garantissait une divergence a la premiere
#: evolution du seuil.
MIN_PYTHON = (3, 12)
PHASE = 1
PHASE_NAME = "Dépôt, configuration, conteneurs, doctor"


def build_info() -> dict[str, str | int]:
    return {
        "version": __version__,
        "phase": PHASE,
        "phase_name": PHASE_NAME,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "executable": sys.executable,
    }
