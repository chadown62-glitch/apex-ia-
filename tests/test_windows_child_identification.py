"""S01/S04 : identification des processus lancés par APEX.exe (audit du 02/10/2026).

Constaté : S04 FAIL au run 13 (un `wsl.exe` enfant à ligne de commande VIDE compté « non autorisé »), PASS au
run 14. Cause dans le script de validation : `Watch-Children` lisait la ligne de commande une seule fois (sondage
WMI toutes les 250 ms, jamais relue) et `Test-AllowedChild` rangeait une ligne vide parmi les interdits — il
confondait « mesure impossible » et « processus interdit ». Or le Panel ne lance que trois formes de `wsl.exe`
(un seul point de création de processus, `RealProcessRunner`, trois appels dans `BackendHost.cs`).

Règles exprimées ici :
  - un enfant identifié est autorisé ou interdit selon la liste existante (inchangée) ;
  - un enfant non identifié n'est JAMAIS autorisé : FAIL si l'audit noyau était actif, BLOCKED sinon ;
  - tout programme autre que wsl.exe reste interdit, même sans ligne de commande ;
  - une ligne lue vide est relue ; l'audit 4688 (CI) est une source indépendante, prouvée par un témoin.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "setup" / "packaging" / "windows" / "Test-ApexWindows.ps1"
BACKEND_HOST = REPO / "setup" / "src" / "ApexSetup.Dashboard" / "Services" / "BackendHost.cs"
WORKFLOW = REPO / ".github" / "workflows" / "windows-desktop.yml"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def _function(text: str, name: str) -> str:
    start = text.index(f"function {name}")
    end = text.find("\nfunction ", start + 1)
    return text[start: end if end != -1 else len(text)]


def _check_block(text: str, check_id: str) -> str:
    start = text.index(f"Check '{check_id}' ")
    nxt = re.search(r"\n    Check '|\n}\n", text[start + 1:])
    return text[start: start + 1 + (nxt.start() if nxt else len(text))]


# -- câblage (statique) -----------------------------------------------------------------------------------

def test_an_empty_command_line_is_read_again(script: str) -> None:
    watch = _function(script, "Watch-Children")
    assert "Read-CommandLine $id" in watch
    assert "elseif (-not $seen[$id].cmd -and $cmd)" in watch     # relue aux relevés suivants
    assert "first_read_empty" in watch and "source" in watch


@pytest.mark.parametrize("check_id", ["S01", "S04"])
def test_security_checks_use_the_three_way_verdict(script: str, check_id: str) -> None:
    block = _check_block(script, check_id)
    assert "Get-ChildrenOutcome $children $script:ProcessAudit.Active" in block
    assert "Test-AllowedChild" not in block


def test_s04_product_conditions_still_fail_hard(script: str) -> None:
    block = _check_block(script, "S04")
    assert "if ($s.Cause -ne 'DiscoveryInvalid' -or $pwned -ne 'no') { $st = 'FAIL' }" in block


def test_audit_is_opt_in_admin_only_proven_by_a_witness_and_restored(script: str) -> None:
    enable = _function(script, "Enable-ProcessAudit")
    assert "WindowsBuiltInRole]::Administrator" in enable
    assert "auditpol.exe /backup" in enable and "$marker" in enable and "Active = $false" in enable
    restore = _function(script, "Restore-ProcessAudit")
    assert "auditpol.exe /restore" in restore
    main = script[script.index("\ntry {\n"):]
    assert main.index("if ($AuditProcessCreation)") < main.index("Enable-ProcessAudit")
    finally_block = main[main.index("} finally {"):]
    assert finally_block.index("Restore-ProcessAudit") < finally_block.index("results.json")


def test_ci_enables_the_audit_but_the_user_kit_does_not() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow[workflow.index("Validation script (Windows PowerShell 5.1"):]
    step = step[: step.index("      - name:", 10)]
    assert "-AuditProcessCreation" in step
    for user_file in ("Valider-APEX.cmd", "LISEZ-MOI.txt"):
        assert "AuditProcessCreation" not in (SCRIPT.parent / user_file).read_text(encoding="utf-8-sig")


# -- exécution réelle des fonctions du script (PowerShell) -----------------------------------------------

def _pwsh() -> str | None:
    return os.environ.get("APEX_TEST_PWSH") or shutil.which("pwsh")


def _panel_command_lines() -> list[str]:
    """Les trois formes d'appel du Panel, lues dans BackendHost.cs, telles que CreateProcess les reçoit."""
    source = BACKEND_HOST.read_text(encoding="utf-8")
    assert source.count('processRunner.RunAsync("wsl.exe"') == 3
    assert '["-l", "-v"]' in source and '["-l", "--running", "-q"]' in source
    assert 'args.AddRange(["--exec", "cat", "--", path]);' in source
    tails = [
        "-l -v",
        "-l --running -q",
        "--exec cat -- /home/user/.apex/runtime/backend.json",
        '-d Ubuntu-24.04 --cd ~ --exec cat -- /home/user/apex/.apex/api_token',
        '-d "Ubuntu 24.04" --exec cat -- "/home/user/mon apex/.apex/api_token"',
    ]
    return [f"{exe} {t}" for t in tails for exe in ("wsl.exe", '"wsl.exe"', r'"C:\WINDOWS\system32\wsl.exe"')]


_EVENT_4688 = """<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>
  <System><EventID>4688</EventID></System>
  <EventData>
    <Data Name='SubjectUserName'>runneradmin</Data>
    <Data Name='NewProcessId'>0x1a2c</Data>
    <Data Name='NewProcessName'>C:\\Windows\\System32\\wsl.exe</Data>
    <Data Name='TokenElevationType'>%%1938</Data>
    <Data Name='ProcessId'>0x2f0</Data>
    <Data Name='CommandLine'>"C:\\WINDOWS\\system32\\wsl.exe" -l --running -q</Data>
  </EventData>
</Event>"""

_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:APEX_SCRIPT, [ref]$tokens, [ref]$errors)
foreach ($n in 'Test-AllowedChild', 'Get-ChildVerdict', 'Get-ChildrenOutcome', 'ConvertFrom-ProcessCreationEvent') {
    $f = $ast.Find({ param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq $n }, $true)
    . ([scriptblock]::Create($f.Extent.Text))
}
$case = Get-Content $env:APEX_CASES -Raw | ConvertFrom-Json
$out = [ordered]@{}
$out.panel = @($case.panel | ForEach-Object { Get-ChildVerdict ([ordered]@{ name = 'wsl.exe'; cmd = $_; pid = 1; source = 'wmi' }) })
$c = { param($name, $cmd) [ordered]@{ name = $name; cmd = $cmd; pid = 7; source = 'test' } }
$out.verdicts = [ordered]@{
    empty_wsl = Get-ChildVerdict (& $c 'wsl.exe' '')
    shell_wsl = Get-ChildVerdict (& $c 'wsl.exe' 'wsl.exe -d Ubuntu --exec bash -c "id"')
    cmd_no_cmdline = Get-ChildVerdict (& $c 'cmd.exe' '')
    powershell = Get-ChildVerdict (& $c 'powershell.exe' 'powershell -c calc')
    no_name = Get-ChildVerdict (& $c '' '')
}
$ok = & $c 'wsl.exe' 'wsl.exe -l -v'
$unknown = & $c 'wsl.exe' ''
$bad = & $c 'cmd.exe' 'cmd /c calc'
$out.outcomes = [ordered]@{
    all_allowed = (Get-ChildrenOutcome @($ok, $ok) $false).Status
    unknown_no_audit = (Get-ChildrenOutcome @($ok, $unknown) $false).Status
    unknown_with_audit = (Get-ChildrenOutcome @($ok, $unknown) $true).Status
    forbidden_and_unknown = (Get-ChildrenOutcome @($unknown, $bad) $false).Status
    nothing_seen = (Get-ChildrenOutcome @() $false).Status
}
$out.detail_unknown = (Get-ChildrenOutcome @($ok, $unknown) $false).Detail
$e = ConvertFrom-ProcessCreationEvent ([xml]$case.event)
$out.event = [ordered]@{ pid = $e.pid; parent = $e.parent; name = $e.name; cmd = $e.cmd }
$out | ConvertTo-Json -Depth 5 -Compress
"""


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory) -> dict:
    pwsh = _pwsh()
    if not pwsh:
        pytest.skip("PowerShell absent (définir APEX_TEST_PWSH) : seules les vérifications statiques s'exécutent")
    tmp = tmp_path_factory.mktemp("childid")
    cases = tmp / "cases.json"
    cases.write_text(json.dumps({"panel": _panel_command_lines(), "event": _EVENT_4688}), encoding="utf-8")
    harness = tmp / "harness.ps1"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(harness)], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "APEX_SCRIPT": str(SCRIPT), "APEX_CASES": str(cases)})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_every_panel_command_shape_is_allowed(ran: dict) -> None:
    """Un enfant du Panel IDENTIFIÉ est toujours autorisé : un FAIL avec ligne lisible serait un vrai écart."""
    assert set(ran["panel"]) == {"allowed"}


def test_unreadable_command_line_is_never_allowed(ran: dict) -> None:
    assert ran["verdicts"]["empty_wsl"] == "unidentified"
    assert ran["verdicts"]["no_name"] == "unidentified"


def test_forbidden_programs_stay_forbidden_even_without_command_line(ran: dict) -> None:
    assert ran["verdicts"]["shell_wsl"] == "forbidden"
    assert ran["verdicts"]["cmd_no_cmdline"] == "forbidden"
    assert ran["verdicts"]["powershell"] == "forbidden"


def test_outcome_never_passes_on_an_unidentified_child(ran: dict) -> None:
    outcomes = ran["outcomes"]
    assert outcomes["all_allowed"] == "PASS"
    assert outcomes["unknown_no_audit"] == "BLOCKED"
    assert outcomes["unknown_with_audit"] == "FAIL"
    assert outcomes["forbidden_and_unknown"] == "FAIL"
    assert outcomes["nothing_seen"] == "PASS"   # inchangé : ce qui n'est pas vu n'est pas prouvé absent (cf. H01)
    assert "jamais comptée sûre" in ran["detail_unknown"] and "-AuditProcessCreation" in ran["detail_unknown"]


def test_process_creation_event_is_parsed(ran: dict) -> None:
    assert ran["event"] == {"pid": 0x1A2C, "parent": 0x2F0, "name": r"C:\Windows\System32\wsl.exe",
                            "cmd": r'"C:\WINDOWS\system32\wsl.exe" -l --running -q'}
