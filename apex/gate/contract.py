"""Contrat de lecture du rapport d'environnement par les phases suivantes.

Ce module est le point d'entrée unique pour toute phase qui a besoin de
connaître l'état réel de la machine. Il applique trois règles :

1. Un rapport SIMULATED n'autorise aucune décision de capacité.
2. Une capacité inconnue (`null`) n'est pas une capacité absente : elle lève
   une erreur explicite plutôt que de se comporter comme `False`.
3. Une empreinte matérielle différente de celle enregistrée invalide la
   comparabilité des expériences et doit être signalée.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex.errors import ApexError, IsolationUnavailable
from apex.gate.report import DEFAULT_REPORT_PATH, SCHEMA_VERSION


class EnvironmentUnknown(ApexError):
    """Le rapport d'environnement est absent, simulé, ou muet sur ce point."""

    exit_code = 8


@dataclass(frozen=True)
class Environment:
    raw: dict[str, Any]

    @property
    def measured_on_target(self) -> bool:
        return bool(self.raw.get("measured_on_target_machine"))

    @property
    def fingerprint(self) -> str:
        return str(self.raw.get("fingerprint", ""))

    @property
    def machine_label(self) -> str | None:
        return self.raw.get("machine_label")

    def assert_matches(self, expected_fingerprint: str) -> None:
        """Refuse un rapport mesure sur une autre machine.

        Un rapport reste un fichier : rien n'empeche de le copier d'une machine
        a l'autre. L'empreinte est ce qui rend cette copie inoperante plutot
        que trompeuse.
        """
        if self.fingerprint != expected_fingerprint:
            raise EnvironmentUnknown(
                "rapport mesure sur une autre machine "
                f"({self.fingerprint} != {expected_fingerprint}) — "
                "relancer « apex gate » sur celle-ci"
            )

    def capability(self, name: str) -> bool:
        """Retourne une capacité, ou lève si elle est inconnue.

        Le refus de répondre est délibéré : une phase qui traiterait `null`
        comme `False` désactiverait une fonctionnalité disponible, ou pire,
        en activerait une non vérifiée.
        """
        if not self.measured_on_target:
            raise EnvironmentUnknown(
                "rapport d'environnement simulé — exécuter « apex gate » sur la machine cible"
            )
        value = self.raw.get("capabilities", {}).get(name, None)
        if value is None:
            raise EnvironmentUnknown(
                f"capacité « {name} » non mesurée — relancer le Gate avec les autorisations requises"
            )
        return bool(value)

    def require(self, name: str, *, feature: str, required: str) -> None:
        """Exige une capacité pour activer une fonctionnalité, sinon la désactive."""
        if not self.capability(name):
            raise IsolationUnavailable(
                feature, required=required,
                available=str(self.raw.get("capabilities", {}).get("isolation_level")),
            )


def list_environments(root: str | Path = ".apex/environments") -> list[Environment]:
    """Tous les environnements mesures, une entree par machine."""
    directory = Path(root)
    if not directory.is_dir():
        return []
    out = []
    for item in sorted(directory.glob("*.json")):
        try:
            out.append(Environment(json.loads(item.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def load_environment(
    path: str | Path = DEFAULT_REPORT_PATH, *, expected_fingerprint: str | None = None
) -> Environment:
    file_path = Path(path)
    if not file_path.is_file():
        raise EnvironmentUnknown(
            f"rapport d'environnement absent ({file_path}) — exécuter « apex gate »"
        )
    document = json.loads(file_path.read_text(encoding="utf-8"))
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise EnvironmentUnknown(
            f"schéma de rapport incompatible : {version} (attendu {SCHEMA_VERSION})"
        )
    environment = Environment(document)
    if expected_fingerprint is not None:
        environment.assert_matches(expected_fingerprint)
    return environment
