"""Régressions issues du run 11 (Windows Server 2025 + WSL2 réel, commit f694541).

1. `launch.sh serve` s'est arrêté sur `apex gate` non concluant (code 5) : le backend n'a jamais démarré et
   dix scénarios backend ont été comptés FAIL en cascade (B09 a même attendu 20 min) au lieu d'être BLOCKED
   avec la cause exacte.
2. Le run 12 avait contourné le point 1 en CI par un gate.exceptions.yaml temporaire. Depuis l'audit du
   02/10/2026, le démarrage ne dépend plus de l'autorisation de phase 2 (voir
   tests/test_startup_gate_contract.py) : la CI ne doit plus écrire AUCUNE dérogation.
3. S05 : le faux serveur (Start-Job) se liait pendant que le parent tenait le port et fermait sans lire la
   requête ; le Panel voyait ApiTimeout au lieu de PortConflict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "setup" / "packaging" / "windows" / "Test-ApexWindows.ps1"
PREPARE = REPO / "setup" / "packaging" / "windows" / "ci" / "prepare_wsl_backend.sh"


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
    for name in ("EXIT_DOCKER_FAIL", "EXIT_PYTHON_MISSING", "EXIT_DOCTOR_FAIL", "EXIT_GATE_FAIL",
                 "EXIT_GATE_UNVERIFIED"):
        assert re.search(rf"^\s+{codes[name]} \{{ \$reason = ", refusal, re.M), name
    # une panne interne du Gate n'est pas un refus de la machine : elle reste un FAIL de B03
    assert not re.search(rf"^\s+{codes['EXIT_GATE_ERROR']} \{{", refusal, re.M)
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


# -- 2. plus aucune dérogation du Gate en CI ------------------------------------------------------------

def test_ci_preparation_writes_no_gate_exception() -> None:
    text = PREPARE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "gate.exceptions" not in code
    assert not (REPO / "setup" / "packaging" / "windows" / "ci" / "gate_exceptions_for_ci.py").exists()


# -- 3. S05 : faux serveur servi par le script lui-même --------------------------------------------------

def test_s05_fake_server_is_in_process_and_reads_the_request(script: str) -> None:
    s05 = _check_block(script, "S05")
    assert "Start-Job -" not in s05
    assert "$listener.Start()" in s05
    assert s05.index("$listener.Start()") < s05.index("[Diagnostics.Process]::Start($psi)")
    assert "$stream.Read(" in s05 and "SocketShutdown]::Send" in s05
    assert "requêtes servies par le faux serveur" in s05
