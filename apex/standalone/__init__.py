"""APEX AUTONOME (profil « bureau ») — APEX sur un poste, sans WSL, sans Docker, sans serveur externe.

C'est le profil de l'application Windows `APEX.exe` (et, à l'identique, d'un poste Linux sans infrastructure) :
le même Runtime, le même TaskLoop, les mêmes outils, la même mémoire et le même Judge que le profil serveur,
avec seulement ce qui change quand rien n'est installé à côté d'APEX :

- **données** sous un seul dossier par utilisateur, `APEX_HOME` (Windows : `%LOCALAPPDATA%\\APEX`) ;
- **base** SQLite embarquée (`APEX_DATABASE_URL`) au lieu de PostgreSQL conteneurisé ;
- **modèle** natif (backend `transformers`, en processus), poids dans `APEX_HOME/models` ;
- **isolation** du code exécuté : backend propre à l'hôte, toujours vérifié empiriquement avant usage
  (Windows : AppContainer + Job Object, `apex.sandbox.windows_appcontainer`) — jamais d'exécution sans lui ;
- **Doctor et Startup Gate** conscients du profil : ce qui relève de l'infrastructure serveur (WSL2, Docker,
  compose, cgroups) est mesuré et affiché, jamais exigé ; rien n'est déclaré réussi sans mesure.

Le profil est choisi EXPLICITEMENT par `APEX_PROFILE=standalone` (posé par le lanceur `python -m
apex.standalone`), jamais deviné : sans lui, tout se comporte exactement comme avant (profil serveur).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROFILE_ENV = "APEX_PROFILE"
STANDALONE = "standalone"
HOME_ENV = "APEX_HOME"
DEFAULT_PORT = 8000


def is_standalone(environ: dict[str, str] | None = None) -> bool:
    return (environ if environ is not None else os.environ).get(PROFILE_ENV, "").strip().lower() == STANDALONE


def default_home() -> Path:
    """Dossier des données d'APEX autonome, par utilisateur (jamais à côté du programme installé)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "APEX"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "apex"


def app_root() -> Path:
    """Racine de l'application (le dossier qui contient le paquet `apex`, `config.standalone.yaml`, l'interface)."""
    return Path(__file__).resolve().parents[2]


def ui_dist(root: Path) -> Path:
    """Interface web construite : `frontend/dist` dans le dépôt, `frontend_dist` dans l'artefact Runtime
    (`runtime_build`) que l'installateur Windows embarque."""
    packaged = root / "frontend_dist"
    return packaged if packaged.is_dir() and not (root / "frontend" / "dist").is_dir() else root / "frontend" / "dist"


@dataclass(frozen=True)
class StandalonePaths:
    home: Path

    @property
    def data_root(self) -> Path:
        return self.home / "data"

    @property
    def models_dir(self) -> Path:
        return self.home / "models"

    @property
    def runtime_dir(self) -> Path:
        """Enregistrement de découverte, journal de démarrage, verrou — lus par le Panel aux MÊMES chemins
        relatifs que dans le profil serveur (`.apex/runtime/…`, avec `APEX_HOME` pour dossier de base)."""
        return self.home / ".apex" / "runtime"

    @property
    def database_path(self) -> Path:
        return self.data_root / "apex.db"

    @property
    def launch_log(self) -> Path:
        return self.runtime_dir / "launch.log"

    def directories(self) -> tuple[Path, ...]:
        return (self.home, self.data_root, self.data_root / "workspaces", self.data_root / "artifacts",
                self.data_root / "logs", self.models_dir, self.runtime_dir)

    def ensure(self) -> None:
        for directory in self.directories():
            directory.mkdir(parents=True, exist_ok=True)


def paths(environ: dict[str, str] | None = None) -> StandalonePaths:
    env = environ if environ is not None else os.environ
    home = env.get(HOME_ENV)
    return StandalonePaths(Path(home) if home else default_home())


def sqlite_url(path: Path) -> str:
    # `sqlite:///` + chemin absolu ; sous Windows « C:\\… » devient « C:/… » (forme acceptée par SQLAlchemy).
    return "sqlite:///" + path.resolve().as_posix()


def environment(home: Path | None = None, *, root: Path | None = None) -> dict[str, str]:
    """Variables qui DÉFINISSENT le profil autonome pour ce processus et ses enfants — un seul endroit."""
    p = StandalonePaths(home) if home is not None else paths()
    root = root or app_root()
    return {
        PROFILE_ENV: STANDALONE,
        HOME_ENV: str(p.home),
        "APEX_DATA_ROOT": str(p.data_root),
        "APEX_MODELS_DIR": str(p.models_dir),
        "APEX_DISCOVERY_DIR": str(p.runtime_dir),
        "APEX_DATABASE_URL": sqlite_url(p.database_path),
        "APEX_CONFIG": str(root / "config.standalone.yaml"),
        "APEX_UI_DIST": str(ui_dist(root)),
        # `.env` du dépôt (profil serveur) jamais lu : le profil autonome n'a aucun secret à charger.
        "APEX_ENV_FILE": str(p.home / "apex.env"),
    }


def apply_environment(home: Path | None = None) -> dict[str, str]:
    """Pose les variables du profil pour ce processus et ses enfants. Elles PRIMENT sur l'environnement hérité
    (un `APEX_DATA_ROOT` ou un `APEX_CONFIG` du profil serveur ne doit jamais mélanger les deux profils) ;
    seul `APEX_HOME` choisit le dossier des données."""
    values = environment(home)
    os.environ.update(values)
    return values
