"""Capacité `real_windows` : résultats produits sur un Windows réel — jamais un PASS construit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from apex.improvement.__main__ import CAPABILITIES
from apex.improvement.capabilities import real_windows as rw


def _check(cid: str, status: str, run: int = 1) -> dict[str, Any]:
    return {
        "run": run,
        "id": cid,
        "category": "x",
        "title": cid,
        "status": status,
        "detail": f"{cid} {status}",
        "data": {},
    }


def _results(
    tmp_path: Path,
    checks: list[dict[str, Any]],
    *,
    repeat: int = 2,
    wine: bool = False,
    script: str | None = None,
) -> Path:
    data = {
        "schema": 1,
        "script_sha256": script
        if script is not None
        else hashlib.sha256(rw.SCRIPT.read_bytes()).hexdigest(),
        "repeat": repeat,
        "environment": {
            "label": "t",
            "os_caption": "Microsoft Windows 11 Pro",
            "os_build": "26100",
            "os_is_server": False,
            "is_wine": wine,
            "distro_under_test": "Ubuntu",
            "installer": {"sha256": "ab"},
        },
        "checks": checks,
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _all_pass(run: int) -> list[dict[str, Any]]:
    return [_check(i, "PASS", run) for _, ids in rw.GROUPS.values() for i in ids if not i.startswith("R")]


def test_registered_with_one_metric_per_mission_group() -> None:
    entry = CAPABILITIES["real_windows"]
    assert {m.name for m in entry.adapter().spec.metrics} == set(rw.GROUPS)
    assert entry.in_process_judge().judge_id == "ci-judge:real-windows"


def test_a_blocked_check_blocks_its_group_and_is_never_counted_as_passed() -> None:
    checks = [
        _check("I12", "PASS"),
        _check("B04", "BLOCKED"),
        _check("I03", "PASS"),
        _check("I04", "PASS"),
        _check("I05", "FAIL"),
    ]
    values, blocked, _ = rw.group_values(checks, 1)
    assert values["uninstall"] == 1.0
    assert "ready" in blocked and "B04" in blocked["ready"]
    assert values["shortcut"] == pytest.approx(2 / 3)
    assert "install" in blocked  # vérifications absentes = non exécutées, jamais réussies


def test_not_applicable_is_excluded_but_a_group_of_only_not_applicable_is_blocked() -> None:
    values, blocked, _ = rw.group_values([_check("B04", "NOT_APPLICABLE")], 1)
    assert "ready" not in values and "ready" in blocked


def test_two_real_runs_give_two_measures_and_one_run_never_fakes_reproducibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APEX_REAL_WINDOWS_RESULTS", str(_results(tmp_path, _all_pass(1), repeat=1)))
    monkeypatch.delenv("APEX_REAL_WINDOWS_REBOOT", raising=False)
    adapter = rw.RealWindowsAdapter()
    first, second = adapter.measure("v"), adapter.measure("v")
    assert first.samples["install"] == (1.0,)
    assert second.samples == {} and "reproductibilité non vérifiée" in second.blocked["install"]
    assert "restart" in first.blocked  # aucun redémarrage réel fourni


def test_provenance_probe_refuses_wine_and_an_edited_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APEX_REAL_WINDOWS_RESULTS", str(_results(tmp_path, _all_pass(1))))
    assert rw._probe_provenance().refuted is False
    monkeypatch.setenv("APEX_REAL_WINDOWS_RESULTS", str(_results(tmp_path, _all_pass(1), wine=True)))
    assert rw._probe_provenance().refuted is True
    monkeypatch.setenv("APEX_REAL_WINDOWS_RESULTS", str(_results(tmp_path, _all_pass(1), script="0" * 64)))
    assert rw._probe_provenance().refuted is True


def test_a_failed_security_check_is_a_refutation_and_blocked_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APEX_REAL_WINDOWS_RESULTS", str(_results(tmp_path, [_check("W03", "FAIL")])))
    assert rw._PROBES[1][3]().refuted is True
    monkeypatch.setenv("APEX_REAL_WINDOWS_RESULTS", str(_results(tmp_path, [_check("W03", "BLOCKED")])))
    assert rw._PROBES[1][3]().refuted is None
