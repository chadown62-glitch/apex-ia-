"""Contrat `DesktopAutomationProvider` — même discipline que `ModelProvider`/
`SandboxProvider`/`ArtifactStoreProvider` : un seul point que connaît
l'appelant, un backend concret (`apex.desktop.pyautogui_backend`) qui
l'implémente, jamais de logique d'autorisation dupliquée dans un backend —
`execute()` doit TOUJOURS passer par `apex.desktop.allowlist.
is_action_permitted` avant d'agir, ici même dans la classe abstraite via
`BaseDesktopAutomationProvider`, pour qu'un nouveau backend futur ne puisse
pas l'oublier."""

from __future__ import annotations

from abc import ABC, abstractmethod

from apex.desktop.allowlist import is_action_permitted
from apex.desktop.model import (
    AllowlistPolicy,
    AutomationCapabilityReport,
    DesktopAction,
    DesktopActionResult,
    PermissionDecision,
    WindowInfo,
)


class DesktopAutomationProvider(ABC):
    @abstractmethod
    def detect(self) -> AutomationCapabilityReport:
        """Sonde RÉELLE de la disponibilité (écran, bibliothèque
        d'automatisation) — jamais une supposition. Ne lève jamais."""

    @abstractmethod
    def active_window(self) -> WindowInfo | None:
        """La fenêtre RÉELLEMENT active, mesurée maintenant — `None` si
        indéterminable (jamais une valeur par défaut inventée : voir
        `PermissionDecision.DENIED_NO_ACTIVE_WINDOW`, le refus par prudence
        qui en découle dans `is_action_permitted`)."""

    @abstractmethod
    def _execute_allowed(self, action: DesktopAction) -> DesktopActionResult:
        """Exécute RÉELLEMENT `action` — n'est appelé QUE par `execute()`
        ci-dessous, jamais directement, et seulement après que la politique
        a rendu `ALLOWED`. Un backend concret implémente cette méthode,
        jamais `execute()`."""


class BaseDesktopAutomationProvider(DesktopAutomationProvider):
    """Applique la politique pour CHAQUE backend concret — un backend ne
    peut pas accidentellement contourner `is_action_permitted` en oubliant
    de l'appeler, puisqu'il n'implémente que `_execute_allowed()`, jamais
    `execute()` lui-même."""

    def __init__(self, *, policy: AllowlistPolicy) -> None:
        self._policy = policy

    def execute(self, action: DesktopAction) -> DesktopActionResult:
        window = self.active_window()
        decision = is_action_permitted(action, window, self._policy)
        if decision is not PermissionDecision.ALLOWED:
            return DesktopActionResult(
                action=action, decision=decision, executed=False,
                detail=f"action refusée par la politique : {decision.value}",
                facts={"window_title": window.title if window else None},
            )
        return self._execute_allowed(action)
