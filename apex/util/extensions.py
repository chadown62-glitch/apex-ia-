"""Point d'extension EXPLICITE entre le Runtime livré et la recherche (06/10/2026).

Le Runtime (`runtime_build/selection.py`) ne contient pas les paquets de recherche. Les routes HTTP, commandes CLI,
tâches de fond et sondes qui en dépendent vivent dans `apex.research_api` et ne sont montées que si ce paquet est
présent (arbre de développement). Ce module ne connaît RIEN de leur contenu : il charge un module nommé, et
distingue « paquet absent » (Runtime livré : `None`, comportement normal) de toute autre erreur (paquet présent
mais cassé : l'erreur remonte, jamais masquée).
"""

from __future__ import annotations

import importlib
from types import ModuleType

RESEARCH_EXTENSION = "apex.research_api"


def load_research_extension() -> ModuleType | None:
    try:
        return importlib.import_module(RESEARCH_EXTENSION)
    except ModuleNotFoundError as exc:
        if exc.name == RESEARCH_EXTENSION:
            return None
        raise
