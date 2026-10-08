"""Arrêt PROPRE du serveur à la demande (application autonome `APEX.exe` : bouton « Arrêter APEX »).

Sous Windows, il n'existe ni SIGINT entre processus ni `/proc` : `python -m apex.standalone stop` demande
l'arrêt par `POST /api/v1/system/shutdown`, authentifié par le jeton comme toute l'API. `apex serve`
enregistre ici le rappel qui demande à SON serveur uvicorn de s'arrêter — même chemin que Ctrl+C
(fin des requêtes en cours, lifespan d'arrêt, retrait de l'enregistrement de découverte).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

_lock = threading.Lock()
_callback: Callable[[], None] | None = None


def register_shutdown(callback: Callable[[], None] | None) -> None:
    global _callback
    with _lock:
        _callback = callback


def request_shutdown() -> bool:
    """Vrai si un arrêt a été demandé au serveur ; faux si aucun serveur ne l'a rendu possible."""
    with _lock:
        callback = _callback
    if callback is None:
        return False
    callback()
    return True
