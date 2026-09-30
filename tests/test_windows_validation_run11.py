"""Régressions issues du run 11 (Windows Server 2025 + WSL2 réel, commit f694541).

1. `launch.sh serve` s'est arrêté sur `apex gate` non concluant (code 5) : le backend n'a jamais démarré et
   dix scénarios backend ont été comptés FAIL en cascade (B09 a même attendu 20 min) au lieu d'être BLOCKED
   avec la cause exacte.
2. Sur le runner CI, les contrôles du Gate non concluants par nature (GPU absent, sondes que launch.sh
   n'autorise jamais) doivent être excusés par gate.exceptions.yaml — jamais un FAIL.
3. S05 : le faux serveur (Start-Job) se liait pendant que le parent tenait le port et fermait sans lire la
   requête ; le Panel voyait ApiTimeout au lieu de PortConflict.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "setup" / "packaging" / "windows" / "Test-ApexWindows.ps1"
GEN = REPO / "setup" / "packaging" / "windows" / "ci" / "gate_exceptions_for_ci.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gate_exceptions_for_ci", GEN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _check_block(text: str, check_id: str) -> str:
    start = text.index(f"Check '{check_id}' ")
    nxt = re.search(r"\n    Check '|\n}\n", text[start + 1:])
    return text[start: start + 1 + (nxt.start() if nxt else len(text))]


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


# -- 1. préflight refusé : BLOCKED avec la cause, jamais une cascade de FAIL ------------------------------

def test_preflight_refusal_codes_match_launch_sh(script: str) -> None:
    launch = (REPO / "scripts" / "apex-launch.sh").read_text(encoding="utf-8")
    codes = dict(re.findall(r"readonly (EXIT_\w+)=(\d+)", launch))
    refusal = script[script.index("function Get-PreflightRefusal"): script.index("function Skip-IfBackendDown")]
    for name in ("EXIT_DOCKER_FAIL", "EXIT_PYTHON_MISSING", "EXIT_DOCTOR_FAIL", "EXIT_GATE_FAIL"):
        assert re.search(rf"^\s+{codes[name]} \{{ \$reason = ", refusal, re.M), name
    # code 1 est aussi celui d'`apex serve` en échec : n'est une refusal que si le journal le prouve.
    assert "'\\.env introuvable'" in refusal


def test_exit_code_is_observable(script: str) -> None:
    start_launch = script[script.index("function Start-Launch"): script.index("function Get-PreflightRefusal")]
    assert "$null = $p.Handle" in start_launch


def test_b03_is_blocked_not_failed_on_a_preflight_refusal(script: str) -> None:
    b03 = _check_block(script, "B03")
    assert "Get-PreflightRefusal" in b03
    assert "$script:BackendDown = $refusal" in b03
    assert "'BLOCKED'" in b03
    # tout autre échec reste un FAIL
    assert "'FAIL'" in b03


@pytest.mark.parametrize("check_id", ["M01", "B04", "B05", "B06", "B07", "B08", "B09", "B10", "B12", "B13"])
def test_checks_needing_a_running_backend_are_guarded(script: str, check_id: str) -> None:
    assert "Skip-IfBackendDown" in _check_block(script, check_id)


@pytest.mark.parametrize("check_id", ["B01", "B02", "B11"])
def test_checks_not_needing_a_running_backend_still_run(script: str, check_id: str) -> None:
    assert "Skip-IfBackendDown" not in _check_block(script, check_id)


def test_backend_down_is_reset_for_each_run(script: str) -> None:
    body = script[script.index("function Test-Backend"):]
    assert body.index("$script:BackendDown = $null") < body.index("Check 'B01'")


# -- 2. dérogations du Gate en CI ------------------------------------------------------------------------

def _report(*checks):
    return {"checks": [dict(zip(("check_id", "status", "unknown_reason", "message"), c)) for c in checks]}


def test_ci_exceptions_only_cover_inconclusive_by_nature() -> None:
    gen = _load_gen()
    report = _report(
        ("GATE-06", "FAIL", None, "démon Docker injoignable"),
        ("GATE-07", "DEGRADED", None, "CPU seul"),
        ("GATE-08", "UNKNOWN", "not_applicable", "aucun GPU"),
        ("GATE-15", "UNKNOWN", "not_authorized", "sonde réseau non autorisée"),
        ("GATE-30", "UNKNOWN", "probe_error", "erreur de sonde"),
        ("GATE-03", "PASS", None, "Ubuntu"),
    )
    assert [c["check_id"] for c in gen.excusable_checks(report)] == ["GATE-08", "GATE-15"]


def test_ci_exceptions_file_is_read_by_the_real_gate_and_expires_next_day(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    from apex.gate.report import load_exceptions

    gen = _load_gen()
    src = tmp_path / "gate.json"
    src.write_text(json.dumps(_report(
        ("GATE-08", "UNKNOWN", "not_applicable", "modèle « non identifiable » : aucun GPU"),
        ("GATE-22", "UNKNOWN", "not_authorized", "contrôle bureau non autorisé"),
        ("GATE-14", "FAIL", None, "cgroups v2 absent"),
    )), encoding="utf-8")
    out = tmp_path / "gate.exceptions.yaml"
    assert gen.main(["x", str(src), str(out)]) == 0
    exceptions = load_exceptions(out)
    assert [e.check_id for e in exceptions] == ["GATE-08", "GATE-22"]
    assert all(e.expires_on == dt.date.today() + dt.timedelta(days=1) for e in exceptions)
    assert all(e.is_active() for e in exceptions)
    assert not any(e.is_active(dt.date.today() + dt.timedelta(days=2)) for e in exceptions)


def test_ci_exceptions_file_with_nothing_to_excuse_is_valid(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    from apex.gate.report import load_exceptions

    gen = _load_gen()
    src = tmp_path / "gate.json"
    src.write_text(json.dumps(_report(("GATE-03", "PASS", None, "ok"))), encoding="utf-8")
    out = tmp_path / "gate.exceptions.yaml"
    gen.main(["x", str(src), str(out)])
    assert load_exceptions(out) == ()


def test_generator_is_wired_only_in_the_ci_preparation() -> None:
    users = [p.relative_to(REPO).as_posix() for p in (REPO / "setup").rglob("*")
             if p.is_file() and p.suffix in {".sh", ".ps1", ".cmd", ".yml", ".iss"}
             and "gate_exceptions_for_ci" in p.read_text(encoding="utf-8", errors="replace")]
    assert users == ["setup/packaging/windows/ci/prepare_wsl_backend.sh"]


# -- 3. S05 : faux serveur servi par le script lui-même --------------------------------------------------

def test_s05_fake_server_is_in_process_and_reads_the_request(script: str) -> None:
    s05 = _check_block(script, "S05")
    assert "Start-Job -" not in s05
    assert "$listener.Start()" in s05
    assert s05.index("$listener.Start()") < s05.index("[Diagnostics.Process]::Start($psi)")
    assert "$stream.Read(" in s05 and "SocketShutdown]::Send" in s05
    assert "requêtes servies par le faux serveur" in s05
