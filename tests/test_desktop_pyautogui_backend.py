"""Tests de `apex.desktop.pyautogui_backend.PyAutoGuiProvider` — `pyautogui`
est entièrement mocké via `sys.modules` (jamais un vrai écran/import réel
dans cette suite, cohérent avec le fait que cet environnement de
développement n'a structurellement aucun affichage) : ce test prouve le
câblage (detect/active_window/execute → la bonne fonction pyautogui avec
les bons arguments), jamais le comportement RÉEL de pyautogui lui-même."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from apex.desktop.allowlist import build_policy
from apex.desktop.model import AllowedTarget, DesktopAction, DesktopActionKind
from apex.desktop.pyautogui_backend import PyAutoGuiProvider
from apex.obs.capability import CapabilityStatus


def _fake_pyautogui_module(**overrides) -> types.ModuleType:
    mod = types.ModuleType("pyautogui")
    mod.size = MagicMock(return_value=MagicMock(width=1920, height=1080))
    mod.getActiveWindowTitle = MagicMock(return_value="Notepad")
    mod.click = MagicMock()
    mod.doubleClick = MagicMock()
    mod.write = MagicMock()
    mod.press = MagicMock()
    mod.hotkey = MagicMock()
    mod.scroll = MagicMock()
    mod.screenshot = MagicMock()
    for k, v in overrides.items():
        setattr(mod, k, v)
    return mod


class TestDetect(unittest.TestCase):
    def test_a_missing_module_is_unavailable_on_dev_never_unsupported(self) -> None:
        with patch.dict(sys.modules, {"pyautogui": None}):
            provider = PyAutoGuiProvider(policy=build_policy())
            report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_b_no_display_at_import_time_is_unavailable_on_dev(self) -> None:
        """Simule le cas réel d'un hôte sans écran : l'import lui-même lève
        (comportement documenté de pyautogui sur un environnement headless),
        jamais interprété comme une incompatibilité architecturale."""

        class _ExplodingLoader:
            def find_module(self, name, path=None):
                if name == "pyautogui":
                    return self
                return None

            def load_module(self, name):
                raise RuntimeError("no display detected")

        fake_meta_path = [_ExplodingLoader(), *sys.meta_path]
        with (
            patch.dict(sys.modules, {"pyautogui": None}),
            patch.object(sys, "meta_path", fake_meta_path),
        ):
            provider = PyAutoGuiProvider(policy=build_policy())
            report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.UNAVAILABLE_ON_DEV)

    def test_c_screen_measured_is_available(self) -> None:
        with patch.dict(sys.modules, {"pyautogui": _fake_pyautogui_module()}):
            provider = PyAutoGuiProvider(policy=build_policy())
            report = provider.detect()
        self.assertIs(report.status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.facts["screen_width"], 1920)


class TestActiveWindow(unittest.TestCase):
    def test_a_returns_title_from_pyautogui(self) -> None:
        with patch.dict(sys.modules, {"pyautogui": _fake_pyautogui_module()}):
            provider = PyAutoGuiProvider(policy=build_policy())
            window = provider.active_window()
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.title, "Notepad")

    def test_b_missing_module_returns_none_never_raises(self) -> None:
        with patch.dict(sys.modules, {"pyautogui": None}):
            provider = PyAutoGuiProvider(policy=build_policy())
            self.assertIsNone(provider.active_window())

    def test_c_empty_title_returns_none(self) -> None:
        fake = _fake_pyautogui_module(getActiveWindowTitle=MagicMock(return_value=""))
        with patch.dict(sys.modules, {"pyautogui": fake}):
            provider = PyAutoGuiProvider(policy=build_policy())
            self.assertIsNone(provider.active_window())


class TestExecuteWiring(unittest.TestCase):
    def _provider(self) -> PyAutoGuiProvider:
        policy = build_policy((AllowedTarget("Notepad", frozenset(DesktopActionKind)),))
        return PyAutoGuiProvider(policy=policy)

    def test_a_click_calls_pyautogui_click_with_coordinates(self) -> None:
        fake = _fake_pyautogui_module()
        with patch.dict(sys.modules, {"pyautogui": fake}):
            result = self._provider().execute(DesktopAction(kind=DesktopActionKind.CLICK, x=42, y=7))
        self.assertTrue(result.executed)
        fake.click.assert_called_once_with(x=42, y=7)

    def test_b_type_text_calls_pyautogui_write(self) -> None:
        fake = _fake_pyautogui_module()
        with patch.dict(sys.modules, {"pyautogui": fake}):
            result = self._provider().execute(DesktopAction(kind=DesktopActionKind.TYPE_TEXT, text="hello"))
        self.assertTrue(result.executed)
        fake.write.assert_called_once_with("hello")

    def test_c_key_press_single_key_calls_press(self) -> None:
        fake = _fake_pyautogui_module()
        with patch.dict(sys.modules, {"pyautogui": fake}):
            self._provider().execute(DesktopAction(kind=DesktopActionKind.KEY_PRESS, text="enter"))
        fake.press.assert_called_once_with("enter")
        fake.hotkey.assert_not_called()

    def test_d_key_press_combo_calls_hotkey(self) -> None:
        fake = _fake_pyautogui_module()
        with patch.dict(sys.modules, {"pyautogui": fake}):
            self._provider().execute(DesktopAction(kind=DesktopActionKind.KEY_PRESS, text="ctrl+c"))
        fake.hotkey.assert_called_once_with("ctrl", "c")
        fake.press.assert_not_called()

    def test_e_a_pyautogui_exception_is_reported_never_raised(self) -> None:
        fake = _fake_pyautogui_module(click=MagicMock(side_effect=RuntimeError("boom")))
        with patch.dict(sys.modules, {"pyautogui": fake}):
            result = self._provider().execute(DesktopAction(kind=DesktopActionKind.CLICK, x=1, y=1))
        self.assertFalse(result.executed)
        self.assertIn("boom", result.detail)

    def test_f_denied_action_never_calls_pyautogui_at_all(self) -> None:
        fake = _fake_pyautogui_module()
        provider = PyAutoGuiProvider(policy=build_policy())  # policy vide -> tout refusé
        with patch.dict(sys.modules, {"pyautogui": fake}):
            result = provider.execute(DesktopAction(kind=DesktopActionKind.CLICK, x=1, y=1))
        self.assertFalse(result.executed)
        fake.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
