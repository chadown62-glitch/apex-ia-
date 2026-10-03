"""État et mise à niveau du SCHÉMA de la base d'APEX — les migrations Alembic existantes, appliquées.

Constat de l'audit du 03/10/2026 (« première boucle d'exécution réelle ») : `scripts/init-db` ne crée que
les extensions et renvoie rôles et tables à Alembic, mais rien dans le chemin produit (`launch.sh`,
`apex serve`, doctor) n'exécutait jamais `alembic upgrade head`. Sur une installation neuve (PC
utilisateur, WSL de la CI), la base n'avait AUCUNE table : TaskBank, EvalRun, Experience — toute
persistance — échouait, pendant que `/status` affichait « database READY » sur un simple `SELECT 1`.

Ce module ne réécrit aucune migration : il lit `alembic.ini`/`migrations/` du dépôt (la même
configuration que la commande `alembic`, `migrations/env.py` résolvant l'URL et les mots de passe des
rôles depuis l'environnement chargé par `launch.sh`) et :
- `schema_state` compare la révision appliquée à la révision attendue par CE code ;
- `upgrade_to_head` applique les migrations manquantes — vers l'avant uniquement (jamais de
  `downgrade`), idempotent (rien à faire si la base est à jour), aucune donnée supprimée.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

#: Racine du dépôt (là où vivent `alembic.ini` et `migrations/`), indépendante du dossier courant.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SchemaStatus(StrEnum):
    UP_TO_DATE = "up_to_date"
    MISSING = "missing"  # aucune migration appliquée (base neuve)
    BEHIND = "behind"  # révision appliquée plus ancienne que celle du code
    UNKNOWN_REVISION = (
        "unknown_revision"  # révision inconnue de ce code (base plus récente, ou autre lignée)
    )


@dataclass(frozen=True)
class SchemaState:
    status: SchemaStatus
    current: str | None
    head: str

    @property
    def ok(self) -> bool:
        return self.status is SchemaStatus.UP_TO_DATE

    def describe(self) -> str:
        return {
            SchemaStatus.UP_TO_DATE: f"schéma à jour (révision {self.head})",
            SchemaStatus.MISSING: f"schéma absent (aucune migration appliquée ; attendu {self.head})",
            SchemaStatus.BEHIND: f"schéma en retard ({self.current} -> {self.head})",
            SchemaStatus.UNKNOWN_REVISION: (
                f"révision {self.current} inconnue de ce code (attendu {self.head}) : base plus récente "
                "que le dépôt, ou d'une autre lignée"
            ),
        }[self.status]


def _alembic_config() -> Any:
    from alembic.config import Config

    # Sans fichier : `migrations/env.py` ne configure alors pas la journalisation d'`alembic.ini` (INFO,
    # une ligne par migration avec sa docstring) — le journal du démarrage garde une ligne utile par étape.
    # `script_location` est la seule option qu'`alembic.ini` porte pour ce dépôt.
    config = Config()
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("aucune révision Alembic dans migrations/versions")
    return head


def schema_state(engine: Any) -> SchemaState:
    """Lit la révision appliquée (`alembic_version`) — jamais une supposition sur les tables présentes."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("aucune révision Alembic dans migrations/versions")
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current is None:
        status = SchemaStatus.MISSING
    elif current == head:
        status = SchemaStatus.UP_TO_DATE
    else:
        known = {rev.revision for rev in script.walk_revisions()}
        status = SchemaStatus.BEHIND if current in known else SchemaStatus.UNKNOWN_REVISION
    return SchemaState(status=status, current=current, head=head)


def upgrade_to_head() -> None:
    """`alembic upgrade head` — exactement la commande documentée, appliquée en processus."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def engine_from_environment() -> Any:
    """Même résolution d'URL que `migrations/env.py` : `APEX_DATABASE_URL`, sinon config + environnement."""
    import os

    from apex.config.loader import load_config
    from apex.db.engine import build_engine, database_url_from_config

    url = os.environ.get("APEX_DATABASE_URL") or database_url_from_config(
        load_config(os.environ.get("APEX_CONFIG"))
    )
    return build_engine(url)
