"""Authentification locale de l'API — jeton auto-généré, aucun compte.

Audit Phase 0 (voir la conversation) : aucun code du dépôt ne lance jamais
réellement le serveur (aucun `uvicorn ...` documenté) — l'API n'a été
exercée qu'en mémoire via `TestClient`. Le CORS existant (`apex.api.app`)
protège contre une page web dans un navigateur, jamais contre un appel
direct d'un autre processus local — un `curl` ou tout autre exécutable sur
la même machine pouvait atteindre n'importe quelle route sans restriction.

Mécanisme volontairement minimal, proportionné à un runtime local
mono-utilisateur — jamais un service public, jamais un système de comptes :

- un jeton aléatoire cryptographiquement sûr (`secrets.token_urlsafe`),
  généré une seule fois, persisté dans `.apex/api-token` (même convention
  que `.apex/environment.json` — `apex.gate.report` — déjà exclu de Git par
  la règle `.apex/` de `.gitignore`, vérifié avant ce module) ;
- résolution par variable d'environnement `APEX_API_TOKEN` en priorité —
  permet aux tests d'utiliser un jeton éphémère sans jamais lire ni écrire
  le fichier persistant du poste de développement (voir
  `tests/api_test_support.py`) ;
- comparaison à temps constant (`secrets.compare_digest`) — une
  comparaison naïve (`==`) fuiterait la longueur/le préfixe du jeton par
  chronométrage ;
- jamais journalisé : ce module n'appelle aucun logger, et le détail HTTP
  renvoyé sur échec ne cite jamais le jeton fourni ni celui attendu.

Ce que ce module NE fait PAS : il ne mappe pas l'appelant à un `Subject`
de `apex.policy` — voir les docstrings de `apex/api/routes/{agents,
missions,conversations,audit}.py`, toujours exactes sur ce point précis :
tout appelant authentifié agit encore comme `Subject.HUMAN` implicite. Le
jeton prouve seulement « ce processus connaît le secret local », pas une
identité — un mapping plus fin reste un chantier séparé, hors périmètre de
cette étape.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path

from fastapi import Header, HTTPException, Request

#: Chemin relatif — même convention que `apex.gate.report.DEFAULT_REPORT_PATH`
#: (".apex/environment.json") : suppose un répertoire de travail à la racine
#: du dépôt, comme tous les points d'entrée existants (`apex doctor`/`gate`,
#: `scripts/apex-launch.sh`). Aucune nouvelle résolution de racine inventée.
TOKEN_FILE = Path(".apex/api-token")

#: 32 octets (`secrets.token_urlsafe`) => ~256 bits d'entropie, encodés en
#: ~43 caractères URL-safe — largement suffisant pour un secret local,
#: jamais transmis sur le réseau au-delà de la boucle locale.
_TOKEN_BYTES = 32


def _generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _write_token_file(token: str) -> None:
    """Écriture atomique (fichier temporaire + `rename`) — même discipline
    que `record_compose_ownership` du launcher : jamais un fichier
    partiellement écrit visible par un autre processus qui lirait au même
    instant. Permissions restreintes au propriétaire seul, en best-effort
    (`os.chmod` peut ne pas s'appliquer sur tous les systèmes de fichiers —
    ce n'est jamais traité comme une erreur bloquante)."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600 — lecture/écriture propriétaire seul
    tmp.replace(TOKEN_FILE)


def get_or_create_api_token() -> str:
    """Résout le jeton attendu. Jamais journalisé, jamais inclus dans une
    réponse d'erreur — voir `require_api_token`.

    Priorité : `APEX_API_TOKEN` (tests, déploiements futurs contrôlés) >
    fichier persistant `.apex/api-token` (lu s'il existe déjà) > génération
    d'un nouveau jeton, écrit pour les prochains appels."""
    env_token = os.environ.get("APEX_API_TOKEN")
    if env_token:
        return env_token
    if TOKEN_FILE.is_file():
        existing = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = _generate_token()
    _write_token_file(token)
    return token


def require_api_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Dépendance FastAPI — appliquée UNIFORMÉMENT à chaque routeur inclus
    dans `apex.api.app.create_app()`, sans exception : frontière de
    sécurité uniforme, décision explicite (aucune route jugée « assez
    anodine » pour être exemptée au cas par cas).
    `401` dans les deux cas d'échec, sans jamais citer le jeton reçu ou
    attendu dans le message — seule la présence/absence et la validité
    sont observables depuis l'extérieur.

    Second moyen, pour l'interface web servie par APEX (`apex.api.ui`) : une
    session ouverte depuis le Panel par un code à usage unique — cookie
    `HttpOnly` + en-tête `X-APEX-UI` + même origine (`ui.session_authorizes`).
    Un en-tête `Authorization` présent est TOUJOURS jugé seul : un jeton faux
    n'est jamais rattrapé par un cookie."""
    expected = get_or_create_api_token()
    if authorization is None:
        from apex.api.ui import session_authorizes

        if session_authorizes(request):
            return
        raise HTTPException(status_code=401, detail="authentification requise")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="authentification requise")
    provided = authorization.removeprefix("Bearer ").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="jeton invalide")
