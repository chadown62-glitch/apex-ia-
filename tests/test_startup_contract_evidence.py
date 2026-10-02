"""La preuve CI du contrat de démarrage (setup/packaging/windows/ci/startup_contract_evidence.sh) reste alignée
sur les vrais codes : si launch.sh ou `apex gate --startup` changent de contrat, ce test échoue avant que la
CI ne « prouve » un contrat qui n'existe plus."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "setup" / "packaging" / "windows" / "ci" / "startup_contract_evidence.sh"
WORKFLOW = REPO / ".github" / "workflows" / "windows-desktop.yml"


def _expected(label_fragment: str) -> int:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf'expect "[^"]*{re.escape(label_fragment)}[^"]*" (\d+)', text)
    assert match, label_fragment
    return int(match.group(1))


def test_launcher_codes_match_launch_sh() -> None:
    codes = {k: int(v) for k, v in re.findall(r"readonly (EXIT_\w+)=(\d+)",
                                               (REPO / "scripts" / "apex-launch.sh").read_text(encoding="utf-8"))}
    assert _expected("launch.sh (préflight réel)") == codes["EXIT_OK"]
    assert _expected("launch.sh : FAIL mesuré") == codes["EXIT_GATE_FAIL"]
    assert _expected("launch.sh : erreur interne") == codes["EXIT_GATE_ERROR"]


def test_gate_codes_match_the_cli() -> None:
    from apex.cli.main import GATE_STARTUP_UNVERIFIED

    assert _expected("apex gate --startup : précondition") == GATE_STARTUP_UNVERIFIED
    assert _expected("apex gate (phase 2") == 1
    assert _expected("apex serve direct") == 1


def test_evidence_never_creates_a_gate_exception_and_always_runs_in_ci() -> None:
    code = "\n".join(l for l in SCRIPT.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r">\s*\S*gate\.exceptions", code)
    assert "-e \"$REPO/gate.exceptions.yaml\"" in code   # sa présence est un échec
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow[workflow.index("Startup-gate contract on the real WSL2 launcher"):]
    step = step[: step.index("      - name:")]
    assert "if: always()" in step and "startup_contract_evidence.sh" in step
