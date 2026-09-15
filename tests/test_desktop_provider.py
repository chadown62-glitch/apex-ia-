"""Tests de `apex.desktop.provider.BaseDesktopAutomationProvider` — prouve
que TOUT backend concret passe forcément par `is_action_permitted` avant
d'agir, via un double de test minimal (`FakeAutomationProvider`) qui
n'implémente QUE `_execute_allowed`/`active_window`/`detect`, jamais
`execute()` — exactement la contrainte que la classe de base impose."""

from __future__ import annotations

import unittest

from apex.desktop.allowlist import build_policy
from apex.desktop.model import (
    AllowedTarget,
    AutomationCapabilityReport,
    DesktopAction,
    DesktopActionKind,
    DesktopActionResult,
    PermissionDecision,
    WindowInfo,
)
from apex.desktop.provider import BaseDesktopAutomationProvider
from apex.obs.capability import CapabilityStatus


class FakeAutomationProvider(BaseDesktopAutomationProvider):
    """Double de test : `active_window` retourne une valeur programmée,
    `_execute_allowed` enregistre chaque appel RÉEL — jamais un vrai
    pyautogui/écran."""

    def __init__(self, *, policy, window: WindowInfo | None) -> None:
        super().__init__(policy=policy)
        self._window = window
        self.executed_actions: list[DesktopAction] = []

    def detect(self) -> AutomationCapabilityReport:
        return AutomationCapabilityReport(status=CapabilityStatus.AVAILABLE, backend="fake", detail="ok")

    def active_window(self) -> WindowInfo | None:
        return self._window

    def _execute_allowed(self, action: DesktopAction) -> DesktopActionResult:
        self.executed_actions.append(action)
        return DesktopActionResult(
            action=action, decision=PermissionDecision.ALLOWED, executed=True, detail="ok",
        )


class TestBaseProviderEnforcesPolicy(unittest.TestCase):
    def test_a_denied_action_never_reaches_execute_allowed(self) -> None:
        provider = FakeAutomationProvider(
            policy=build_policy(), window=WindowInfo(title="Notepad", process_name="notepad.exe"),
        )
        action = DesktopAction(kind=DesktopActionKind.CLICK, x=1, y=1)
        result = provider.execute(action)
        self.assertFalse(result.executed)
        self.assertIs(result.decision, PermissionDecision.DENIED_NOT_ALLOWLISTED)
        self.assertEqual(provider.executed_actions, [])

    def test_b_allowed_action_reaches_execute_allowed_exactly_once(self) -> None:
        policy = build_policy((AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Notepad", process_name="x")
        provider = FakeAutomationProvider(policy=policy, window=window)
        action = DesktopAction(kind=DesktopActionKind.CLICK, x=1, y=1)
        result = provider.execute(action)
        self.assertTrue(result.executed)
        self.assertEqual(provider.executed_actions, [action])

    def test_c_denylisted_window_never_reaches_execute_allowed_even_with_matching_allowlist(self) -> None:
        policy = build_policy((AllowedTarget("Chrome", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Chrome - Enter your password", process_name="chrome.exe")
        provider = FakeAutomationProvider(policy=policy, window=window)
        result = provider.execute(DesktopAction(kind=DesktopActionKind.CLICK, x=1, y=1))
        self.assertFalse(result.executed)
        self.assertIs(result.decision, PermissionDecision.DENIED_DENYLISTED)
        self.assertEqual(provider.executed_actions, [])

    def test_d_screenshot_reaches_execute_allowed_without_any_window(self) -> None:
        provider = FakeAutomationProvider(policy=build_policy(), window=None)
        result = provider.execute(DesktopAction(kind=DesktopActionKind.SCREENSHOT))
        self.assertTrue(result.executed)
        self.assertEqual(len(provider.executed_actions), 1)


if __name__ == "__main__":
    unittest.main()
