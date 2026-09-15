"""`PyAutoGuiProvider` — le seul backend `DesktopAutomationProvider`
réellement implémenté. Même discipline que `OllamaProvider` (§72) :
`detect()` sonde RÉELLEMENT (jamais une supposition), `UNAVAILABLE_ON_DEV`
jamais `UNSUPPORTED`, aucune action n'est jamais simulée comme ayant réussi.

`pyautogui` (et `pygetwindow`, dont il dépend pour le titre de fenêtre
active sous Windows) ne sont importés qu'à l'intérieur des méthodes, jamais
au niveau du module : un environnement sans écran (CI, conteneur, cette
machine de développement distante) ne doit jamais empêcher le RESTE d'APEX
de s'importer/démarrer — seule cette capacité précise devient
`UNAVAILABLE_ON_DEV`.

**Non validé physiquement par cette session** (aucun écran réel accessible
depuis cet environnement de développement) — voir `docs/DESKTOP_AUTOMATION.md`
pour le protocole de validation physique à exécuter sur la machine cible."""

from __future__ import annotations

import time
from typing import Any

from apex.desktop.model import (
    AllowlistPolicy,
    AutomationCapabilityReport,
    DesktopAction,
    DesktopActionKind,
    DesktopActionResult,
    PermissionDecision,
    WindowInfo,
)
from apex.desktop.provider import BaseDesktopAutomationProvider
from apex.obs.capability import CapabilityStatus

_SCREENSHOT_TIMEOUT_S = 5.0


class PyAutoGuiProvider(BaseDesktopAutomationProvider):
    def __init__(
        self, *, policy: AllowlistPolicy, screenshot_dir: str = ".apex/desktop-screenshots",
    ) -> None:
        super().__init__(policy=policy)
        self._screenshot_dir = screenshot_dir

    # -- détection ----------------------------------------------------------

    def detect(self) -> AutomationCapabilityReport:
        try:
            import pyautogui  # import réel = la sonde elle-même
        except ImportError:
            return AutomationCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="pyautogui",
                detail="`pyautogui` non installé sur cette machine — jamais interprété comme UNSUPPORTED",
                facts={"reason": "import_error"},
            )
        except Exception as exc:
            # `pyautogui` lève à l'IMPORT sur un hôte sans écran (pas
            # d'affichage X11/Wayland/Windows détectable) — signal réel et
            # distinct d'une absence de paquet, toujours UNAVAILABLE_ON_DEV.
            return AutomationCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="pyautogui",
                detail=f"pyautogui indisponible sur cet hôte (pas d'écran ?) : "
                       f"{exc.__class__.__name__}: {exc}",
                facts={"reason": "no_display"},
            )
        try:
            size = pyautogui.size()
        except Exception as exc:
            return AutomationCapabilityReport(
                status=CapabilityStatus.UNAVAILABLE_ON_DEV, backend="pyautogui",
                detail=f"écran non mesurable : {exc.__class__.__name__}: {exc}",
            )
        return AutomationCapabilityReport(
            status=CapabilityStatus.AVAILABLE, backend="pyautogui",
            detail=f"écran {size.width}x{size.height} détecté",
            facts={"screen_width": size.width, "screen_height": size.height},
        )

    # -- fenêtre active -------------------------------------------------------

    def active_window(self) -> WindowInfo | None:
        try:
            import pyautogui
        except ImportError:
            return None
        title = None
        try:
            title = pyautogui.getActiveWindowTitle()
        except Exception:
            title = None
        if not title:
            return None
        # Le nom de PROCESSUS (par opposition au simple titre) exige des
        # API spécifiques à la plateforme (pywin32 sous Windows) —
        # délibérément best-effort : son absence rend seulement inutilisable
        # une entrée d'allowlist qui EXIGE `process_name` (échec fermé,
        # jamais une correspondance permissive par défaut).
        process_name = self._active_process_name_best_effort()
        return WindowInfo(title=title, process_name=process_name or "", is_focused=True)

    def _active_process_name_best_effort(self) -> str | None:
        try:
            import psutil
            import pygetwindow

            window = pygetwindow.getActiveWindow()
            if window is None or not hasattr(window, "_hWnd"):
                return None
            import ctypes

            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(window._hWnd, ctypes.byref(pid))  # type: ignore[attr-defined]
            return str(psutil.Process(pid.value).name())
        except Exception:
            return None

    # -- exécution ------------------------------------------------------------

    def _execute_allowed(self, action: DesktopAction) -> DesktopActionResult:
        import pyautogui

        try:
            facts = self._perform(pyautogui, action)
        except Exception as exc:
            return DesktopActionResult(
                action=action, decision=PermissionDecision.ALLOWED, executed=False,
                detail=f"échec réel d'exécution : {exc.__class__.__name__}: {exc}",
            )
        return DesktopActionResult(
            action=action, decision=PermissionDecision.ALLOWED, executed=True,
            detail=f"{action.kind.value} exécuté", facts=facts,
        )

    def _perform(self, pyautogui: Any, action: DesktopAction) -> dict[str, object]:
        kind = action.kind
        if kind is DesktopActionKind.CLICK:
            pyautogui.click(x=action.x, y=action.y)
            return {"x": action.x, "y": action.y}
        if kind is DesktopActionKind.DOUBLE_CLICK:
            pyautogui.doubleClick(x=action.x, y=action.y)
            return {"x": action.x, "y": action.y}
        if kind is DesktopActionKind.TYPE_TEXT:
            pyautogui.write(action.text or "")
            return {"chars": len(action.text or "")}
        if kind is DesktopActionKind.KEY_PRESS:
            keys = (action.text or "").split("+")
            if len(keys) > 1:
                pyautogui.hotkey(*keys)
            else:
                pyautogui.press(keys[0])
            return {"keys": action.text}
        if kind is DesktopActionKind.SCROLL:
            pyautogui.scroll(action.scroll_amount or 0, x=action.x, y=action.y)
            return {"amount": action.scroll_amount}
        if kind is DesktopActionKind.SCREENSHOT:
            return self._screenshot(pyautogui)
        if kind is DesktopActionKind.WAIT:
            time.sleep(max(0.0, min(action.duration_s or 0.0, _SCREENSHOT_TIMEOUT_S)))
            return {"waited_s": action.duration_s}
        raise ValueError(f"nature d'action non gérée : {kind}")

    def _screenshot(self, pyautogui: Any) -> dict[str, object]:
        import os

        os.makedirs(self._screenshot_dir, exist_ok=True)
        path = os.path.join(self._screenshot_dir, f"{time.time_ns()}.png")
        pyautogui.screenshot(path)
        return {"path": path}
