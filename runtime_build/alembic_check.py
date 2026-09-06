"""Vérification explicite à 3 issues de `alembic upgrade head` contre un
artefact Runtime construit — jamais un simple booléen qui confondrait
« PostgreSQL absent » avec « migration cassée ».

    PASS                  migration réellement exécutée avec succès sur un
                          PostgreSQL réel.
    SKIP_ENVIRONMENT      PostgreSQL non joignable ou identifiants absents
                          — rien n'a été testé, ce n'est PAS une réussite.
    FAIL                  PostgreSQL disponible mais la migration a échoué.

Aucun mock : soit un PostgreSQL réel répond, soit le contrôle est
explicitement marqué SKIP (jamais maquillé en PASS).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AlembicCheckResult:
    status: str  # "PASS" | "FAIL" | "SKIP_ENVIRONMENT"
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


def check_alembic_upgrade(artifact_dir: Path, *, timeout: float = 60.0) -> AlembicCheckResult:
    """Exécute `alembic upgrade head` en sous-processus DEPUIS `artifact_dir`
    (jamais la chaîne DEV) contre la base de test PostgreSQL dédiée
    (`tests/db_test_support.TEST_DB_NAME`). Recrée/nettoie cette base à
    chaque appel — jamais laissée sale en cas d'échec."""
    from tests import db_test_support as support

    if not support.postgres_testable():
        return AlembicCheckResult(
            status="SKIP_ENVIRONMENT",
            detail="PostgreSQL non joignable ou identifiants absents — rien n'a été exécuté.",
        )

    support.recreate_test_database()
    env = dict(os.environ)
    env["POSTGRES_DB"] = support.TEST_DB_NAME
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=artifact_dir, env=env, capture_output=True, text=True, timeout=timeout,
        )
    finally:
        support.drop_application_roles()
        support.drop_test_database()

    if result.returncode == 0:
        return AlembicCheckResult(status="PASS", detail="alembic upgrade head : succès réel.")
    return AlembicCheckResult(
        status="FAIL",
        detail=f"alembic upgrade head a échoué (code {result.returncode}) :\n{result.stderr[-3000:]}",
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args(argv)
    outcome = check_alembic_upgrade(args.artifact_dir)
    print(f"{outcome.status} — {outcome.detail}")
    return 0 if outcome.status != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
