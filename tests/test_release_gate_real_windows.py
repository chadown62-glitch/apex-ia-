"""Release gate : les éléments « Windows réel » ne passent que sur un Windows 10/11 client réel."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "release_gate", REPO / "setup" / "packaging" / "release_gate.py"
)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules["release_gate"] = gate  # requis par @dataclass
_spec.loader.exec_module(gate)

SCRIPT_SHA = hashlib.sha256(
    (REPO / "setup/packaging/windows/Test-ApexWindows.ps1").read_bytes()
).hexdigest()
IDS = ("L01", "L03")


def _result(
    statuses: dict[str, str], *, server: bool, wine: bool = False, sha: str = SCRIPT_SHA
) -> dict[str, Any]:
    return {
        "script_sha256": sha,
        "environment": {
            "label": "m",
            "os_caption": "Microsoft Windows",
            "os_build": "1",
            "os_is_server": server,
            "is_wine": wine,
        },
        "checks": [{"id": i, "status": s, "run": 1} for i, s in statuses.items()],
    }


def test_no_results_is_blocked() -> None:
    assert gate.real_windows_item("x", IDS, []).status == gate.BLOCKED


def test_windows_server_evidence_alone_stays_blocked_with_its_evidence() -> None:
    item = gate.real_windows_item("x", IDS, [_result({"L01": "PASS", "L03": "PASS"}, server=True)])
    assert item.status == gate.BLOCKED
    assert "Windows Server" in item.evidence and "L01=PASS" in item.evidence


def test_a_client_windows_with_every_check_passed_is_pass() -> None:
    item = gate.real_windows_item("x", IDS, [_result({"L01": "PASS", "L03": "PASS"}, server=False)])
    assert item.status == gate.PASS


def test_any_fail_anywhere_is_fail_and_any_blocked_is_blocked() -> None:
    fail = [
        _result({"L01": "PASS", "L03": "PASS"}, server=False),
        _result({"L01": "FAIL", "L03": "PASS"}, server=True),
    ]
    assert gate.real_windows_item("x", IDS, fail).status == gate.FAIL
    blocked = [_result({"L01": "PASS", "L03": "BLOCKED"}, server=False)]
    assert gate.real_windows_item("x", IDS, blocked).status == gate.BLOCKED


def test_wine_or_an_edited_script_is_refused() -> None:
    assert (
        gate.real_windows_item(
            "x", IDS, [_result({"L01": "PASS", "L03": "PASS"}, server=False, wine=True)]
        ).status
        == gate.FAIL
    )
    assert (
        gate.real_windows_item(
            "x", IDS, [_result({"L01": "PASS", "L03": "PASS"}, server=False, sha="0" * 64)]
        ).status
        == gate.FAIL
    )
