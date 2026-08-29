"""Taxonomie d'erreurs APEX.

Principe directeur (§79 du cahier des charges) : aucun composant ne doit
retourner silencieusement une valeur plausible lorsqu'il n'est pas implémenté.
`NotImplementedYet` porte la phase prévue et le repli disponible, de sorte que
l'état réel du système soit toujours lisible depuis le code et depuis
`apex doctor`.
"""

from __future__ import annotations


class ApexError(Exception):
    """Racine de toutes les erreurs APEX."""

    exit_code: int = 1


class ConfigError(ApexError):
    """Configuration absente, illisible ou invalide."""

    exit_code = 2

    def __init__(self, message: str, *, path: str | None = None, field: str | None = None) -> None:
        self.path = path
        self.field = field
        location = " ".join(p for p in (path, field) if p)
        super().__init__(f"{message} ({location})" if location else message)


class DependencyMissing(ApexError):
    """Une dépendance externe requise est absente ou injoignable."""

    exit_code = 3

    def __init__(self, dependency: str, *, remediation: str | None = None) -> None:
        self.dependency = dependency
        self.remediation = remediation
        msg = f"dépendance manquante : {dependency}"
        if remediation:
            msg += f" — remédiation : {remediation}"
        super().__init__(msg)


class TimeoutExceeded(ApexError):
    """Un budget de temps a été dépassé.

    Toute opération à effet de bord dans APEX possède un timeout (principe 13).
    """

    exit_code = 4

    def __init__(self, operation: str, timeout_s: float) -> None:
        self.operation = operation
        self.timeout_s = timeout_s
        super().__init__(f"timeout dépassé sur « {operation} » après {timeout_s:.1f}s")


class NotImplementedYet(ApexError):
    """Interface définie, implémentation prévue pour une phase ultérieure.

    Volontairement distincte de `NotImplementedError` : elle est structurée,
    interrogeable, et son message nomme la phase et le repli. C'est le mécanisme
    qui empêche un composant d'« exister sur le papier ».
    """

    exit_code = 5

    def __init__(self, component: str, *, phase: int, fallback: str | None = None) -> None:
        self.component = component
        self.phase = phase
        self.fallback = fallback
        msg = f"« {component} » n'est pas implémenté — prévu en phase {phase}"
        if fallback:
            msg += f" ; repli actuel : {fallback}"
        super().__init__(msg)


class PolicyDenied(ApexError):
    """Action refusée par le Policy Engine. Jamais rattrapée silencieusement."""

    exit_code = 6


class IsolationUnavailable(ApexError):
    """Le niveau d'isolation exigé n'est pas disponible sur cet hôte.

    Conséquence directe du principe verrouillé n°4 : une fonctionnalité risquée
    est désactivée ou différée plutôt que d'abaisser les garanties de sécurité.
    """

    exit_code = 7

    def __init__(self, feature: str, *, required: str, available: str) -> None:
        self.feature = feature
        self.required = required
        self.available = available
        super().__init__(
            f"« {feature} » exige l'isolation « {required} » ; "
            f"seul « {available} » est disponible — fonctionnalité désactivée"
        )
