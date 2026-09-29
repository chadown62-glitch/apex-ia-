"""Capacité `desktop_distribution` du Continuous Improvement Engine : câblage et sondes statiques.

Les sondes lourdes (Panel compilé, backend réel, Wine) tournent dans les cycles du Gauntlet
(`python -m apex.improvement ...`), pas dans la suite unitaire.
"""

from __future__ import annotations

from apex.improvement.__main__ import CAPABILITIES
from apex.improvement.capabilities import desktop_distribution as dd
from apex.improvement.model import CycleKind


def test_capability_is_registered_and_consistent() -> None:
    entry = CAPABILITIES["desktop_distribution"]
    adapter = entry.adapter()
    names = {m.name for m in adapter.spec.metrics}
    assert names == {*dd._PANEL_METRICS, *dd._INSTALLER_METRICS, "status_seconds"}
    judge = entry.in_process_judge()
    assert judge.judge_id == "ci-judge:desktop-distribution"
    probes = judge.probes_for(CycleKind.ADVERSARIAL)
    assert {p.probe_id for p in probes} == {name for name, *_ in dd._PROBES}


def test_installer_policy_probe_reads_script_lines_not_comments(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Garde de la sonde : une vraie section [UninstallDelete] est détectée, un commentaire non."""
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    iss = (dd.SETUP / "packaging" / "ApexSetup.iss").read_text(encoding="utf-8")
    (packaging / "ApexSetup.iss").write_text(
        iss + '\n[UninstallDelete]\nType: filesandordirs; Name: "{userappdata}\\APEX"\n'
    )
    (tmp_path / "src" / "ApexSetup.Dashboard").mkdir(parents=True)
    (tmp_path / "src" / "ApexSetup.Dashboard" / "app.manifest").write_text(
        (dd.SETUP / "src" / "ApexSetup.Dashboard" / "app.manifest").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(dd, "SETUP", tmp_path)
    assert dd._probe_installer_policy().refuted is True


def test_static_probes_do_not_refute_the_current_tree() -> None:
    assert dd._probe_no_shell_in_wsl_commands().refuted is False
    assert dd._probe_installer_policy().refuted is False
