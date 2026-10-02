"""Interface web d'APEX servie par `apex serve` lui-même — une seule origine, aucun serveur à part.

Avant : l'interface (`frontend/`, React) ne tournait qu'avec `npm run dev` (Vite, Node.js) et un
jeton figé à la compilation (`VITE_APEX_API_TOKEN`) — rien qu'un utilisateur puisse ouvrir.
Maintenant : `apex serve` sert le build de production (`frontend/dist`, construit par
`scripts/apex-build-ui.sh`) à la racine `/`, et l'ouverture se fait depuis le Panel :

1. le Panel (qui détient déjà le jeton, lu dans WSL) demande un CODE À USAGE UNIQUE
   (`POST /api/v1/ui/launch-code`, jeton exigé), valable `LAUNCH_CODE_TTL_S` secondes ;
2. il ouvre le navigateur sur `/ui/login?code=…` : le code est échangé contre une SESSION —
   cookie `HttpOnly` (jamais lisible par du JavaScript), `SameSite=Strict`, en mémoire du
   processus (un redémarrage d'APEX les invalide toutes : on rouvre depuis le Panel) ;
3. l'interface appelle l'API avec ce cookie ET l'en-tête `X-APEX-UI: 1` (voir
   `session_authorizes`) — jamais le jeton lui-même, qui ne quitte donc jamais le Panel.

Le lien ouvert ne contient qu'un code déjà consommé : retrouvé dans l'historique du navigateur, il
ne donne accès à rien. Les fichiers statiques ne portent aucune donnée : ils sont servis sans
authentification, comme toute application web ; chaque donnée passe par l'API authentifiée.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

SESSION_COOKIE = "apex_ui_session"
UI_HEADER = "x-apex-ui"
LAUNCH_CODE_TTL_S = 60.0
SESSION_TTL_S = 12 * 3600.0
#: Répertoire du build (`npm run build` -> `frontend/dist`) ; relatif au dossier de travail d'`apex
#: serve` (la racine du dépôt, comme `.apex/api-token`). `APEX_UI_DIST` pour un autre emplacement.
UI_DIST_ENV = "APEX_UI_DIST"
BUILD_COMMAND = "bash scripts/apex-build-ui.sh"


def ui_dist() -> Path:
    return Path(os.environ.get(UI_DIST_ENV) or "frontend/dist")


def ui_state() -> tuple[bool, str]:
    """(interface disponible, détail) — mesuré à chaque appel, jamais supposé."""
    index = ui_dist() / "index.html"
    if index.is_file():
        return True, f"interface web servie par APEX ({ui_dist()})"
    return False, f"interface web non construite ({index} absent) — {BUILD_COMMAND}"


class _Store:
    """Codes à usage unique et sessions, en mémoire. Comparaisons par recherche de clé exacte d'un
    secret de 256 bits : rien à deviner, rien à chronométrer d'exploitable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._codes: dict[str, float] = {}
        self._sessions: dict[str, float] = {}

    def new_code(self) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._codes[code] = time.monotonic() + LAUNCH_CODE_TTL_S
        return code

    def redeem(self, code: str) -> str | None:
        """Consomme le code (une seule fois, avant expiration) et ouvre une session."""
        with self._lock:
            self._purge()
            if self._codes.pop(code, None) is None:
                return None
            session = secrets.token_urlsafe(32)
            self._sessions[session] = time.monotonic() + SESSION_TTL_S
            return session

    def valid(self, session: str) -> bool:
        with self._lock:
            expiry = self._sessions.get(session)
            if expiry is None:
                return False
            if expiry < time.monotonic():
                del self._sessions[session]
                return False
            return True

    def close(self, session: str) -> None:
        with self._lock:
            self._sessions.pop(session, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for table in (self._codes, self._sessions):
            for key in [k for k, expiry in table.items() if expiry < now]:
                del table[key]


store = _Store()


def session_authorizes(request: Request) -> bool:
    """Une requête de l'interface est authentifiée par sa session SEULEMENT si :
    - le cookie de session est valide ;
    - l'en-tête `X-APEX-UI: 1` est présent : un formulaire ou une image d'un autre site ne peut pas
      le poser, et un `fetch` d'une autre origine qui le pose déclenche un contrôle CORS préalable,
      refusé (aucune origine n'est autorisée à envoyer des cookies, `allow_credentials` absent) ;
    - la requête vient de la même origine (`Origin`/`Sec-Fetch-Site`, quand le navigateur les
      envoie) : une page servie par un AUTRE port de cette machine est « même site » pour
      `SameSite`, jamais « même origine »."""
    session = request.cookies.get(SESSION_COOKIE)
    if not session or request.headers.get(UI_HEADER) != "1":
        return False
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
        return False
    return store.valid(session)


# -- API : code d'ouverture (jeton exigé, routeur inclus sous /api/v1 avec la dépendance commune) --

api_router = APIRouter(prefix="/ui", tags=["ui"])


@api_router.post("/launch-code")
def create_launch_code() -> dict[str, object]:
    available, detail = ui_state()
    code = store.new_code()
    return {
        "status": "real",
        "data": {
            "login_path": f"/ui/login?code={code}",
            "expires_in_s": int(LAUNCH_CODE_TTL_S),
            "ui_available": available,
            "ui_detail": detail,
        },
        "detail": None,
    }


# -- Pages (hors /api/v1, sans jeton : aucune donnée) ------------------------------------------------

page_router = APIRouter(include_in_schema=False)

_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>APEX</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;line-height:1.5}}
code{{background:#8882;padding:.1rem .3rem;border-radius:.2rem}}</style></head>
<body><h1>{title}</h1><p>{body}</p></body></html>"""


def _page(status: int, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=title, body=body), status_code=status,
                        headers={"Cache-Control": "no-store"})


@page_router.get("/ui/login")
def login(request: Request, code: str = "") -> Response:
    session = store.redeem(code) if code else None
    if session is None:
        return _page(401, "Lien d'ouverture expiré ou déjà utilisé",
                     "Rouvrez APEX depuis le Panel (bouton « Ouvrir APEX ») : chaque ouverture crée un "
                     f"lien valable {int(LAUNCH_CODE_TTL_S)} secondes, utilisable une seule fois.")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, session, max_age=int(SESSION_TTL_S), httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@page_router.post("/ui/logout")
def logout(request: Request) -> Response:
    session = request.cookies.get(SESSION_COOKIE)
    if session and request.headers.get(UI_HEADER) == "1":
        store.close(session)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@page_router.get("/{path:path}")
def spa(path: str) -> Response:
    """Fichiers du build, sinon `index.html` (routes de l'interface : /agents, /system…). Jamais
    sous /api/ : une route d'API inconnue reste un 404 JSON, pas une page."""
    if path.split("/", 1)[0] in _NOT_PAGES:
        return Response(status_code=404, content='{"detail":"Not Found"}', media_type="application/json")
    available, detail = ui_state()
    if not available:
        return _page(503, "Interface APEX non construite",
                     f"APEX fonctionne, mais son interface web n'a pas encore été construite sur cette "
                     f"machine ({detail}). Dans le dossier APEX (WSL) : <code>{BUILD_COMMAND}</code>, "
                     "puis rouvrez APEX depuis le Panel.")
    root = ui_dist().resolve()
    if path:
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if "." in path.rsplit("/", 1)[-1]:
            return Response(status_code=404)  # fichier absent du build : jamais la page à la place
    return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


#: Jamais des pages de l'interface : l'API (404 JSON pour une route inconnue) et la documentation
#: interactive de FastAPI, désactivée par défaut (`apex.api.app`) — elle doit le rester (404).
_NOT_PAGES = frozenset({"api", "docs", "redoc", "openapi.json"})
