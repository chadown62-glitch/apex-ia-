"""Socle de `apex doctor`.

Contraintes de conception (§53 et principe 13) :

- **Lecture seule.** Un check propose un diagnostic et une remédiation ; il ne
  modifie jamais le système. La remédiation est un texte destiné à l'humain.
- **Timeout obligatoire.** Un check qui pend ne doit pas bloquer le diagnostic.
- **Résultat structuré.** Chaque résultat porte des faits exploitables, pas
  seulement une phrase — c'est ce qui permet la sortie JSON et, plus tard,
  l'affichage dans le Dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from apex.config.schema import ApexConfig
from apex.util.probe import Probe


class Severity(str, Enum):
    #: Tout va bien.
    PASS = "pass"
    #: Le système fonctionne mais sur un chemin dégradé, ou une bonne pratique
    #: n'est pas respectée. Ne bloque pas la phase.
    WARN = "warn"
    #: Bloquant pour la phase en cours.
    FAIL = "fail"
    #: Vérification impossible dans ce contexte (dépendance absente, autre OS).
    SKIP = "skip"

    @property
    def rank(self) -> int:
        return {"skip": 0, "pass": 1, "warn": 2, "fail": 3}[self.value]


@dataclass(frozen=True)
class CheckResult:
    name: str
    category: str
    severity: Severity
    message: str
    remediation: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "severity": self.severity.value,
            "message": self.message,
            "remediation": self.remediation,
            "facts": self.facts,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class CheckContext:
    """Tout ce qu'un check peut toucher. Rien d'autre n'est accessible."""

    probe: Probe
    config: ApexConfig | None = None
    config_error: str | None = None
    project_root: str = "."
    timeout_s: float = 5.0


class Check(Protocol):
    name: str
    category: str

    def run(self, ctx: CheckContext) -> CheckResult: ...


@dataclass
class SimpleCheck:
    """Adaptateur permettant de déclarer un check comme une simple fonction."""

    name: str
    category: str
    func: Any

    def run(self, ctx: CheckContext) -> CheckResult:
        return self.func(ctx)


def result(
    ctx_name: str,
    category: str,
    severity: Severity,
    message: str,
    *,
    remediation: str = "",
    **facts: Any,
) -> CheckResult:
    return CheckResult(
        name=ctx_name,
        category=category,
        severity=severity,
        message=message,
        remediation=remediation,
        facts=facts,
    )
