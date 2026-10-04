<#
.SYNOPSIS
  Preuve de la première boucle d'exécution réelle d'APEX sur LA MACHINE CIBLE (Windows + WSL2 + Ollama réel).

.DESCRIPTION
  Pilote le Panel installé (APEX.exe --start/--stop : launch.sh, Startup Gate, jamais contourné) et
  setup/packaging/windows/ci/first_real_loop.py, qui ne fait QUE demander à l'APEX réel d'exécuter la tâche de
  référence puis relire ce que la base a enregistré :
    TASK -> TaskRun -> modèle sélectionné par le routage -> génération Ollama -> sandbox -> Judge -> Experience
    -> redémarrage d'APEX -> relecture -> exécution coupée par un arrêt -> « interrupted » après redémarrage.
  Aucune donnée n'est fabriquée ; un maillon non exercé est BLOCKED, jamais PASS.

  Ce script n'installe rien, ne modifie ni .env ni config.yaml, et n'utilise que les modèles déjà présents.
  Il ARRÊTE et REDÉMARRE APEX (d'où -AllowRestart, obligatoire). Le journal complet est écrit dans
  %USERPROFILE%\Documents\APEX-preuve-boucle-<date>\journal.txt (aucun secret : le jeton n'est jamais affiché).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Prouver-Boucle-APEX.ps1 -AllowRestart
#>
param(
    [switch]$AllowRestart,
    [string]$Distro = '',
    [string]$ApexPathInWsl = 'apex',
    [string]$ApexExe = (Join-Path $env:LOCALAPPDATA 'Programs\APEX\APEX.exe')
)
$ErrorActionPreference = 'Stop'
if (-not $AllowRestart) {
    Write-Host 'Ce script arrête puis redémarre APEX. Relancez-le avec -AllowRestart pour confirmer.'
    exit 2
}
if (-not (Test-Path $ApexExe)) { Write-Host "APEX.exe introuvable : $ApexExe (option -ApexExe)"; exit 2 }

$OutDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) ('APEX-preuve-boucle-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $OutDir | Out-Null
Start-Transcript -Path (Join-Path $OutDir 'journal.txt') | Out-Null

function Panel([string]$Action) {
    # Attendre APEX.exe SEUL : --start laisse volontairement tourner le backend (wsl.exe ... panel-serve).
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ApexExe; $psi.Arguments = $Action
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true; $psi.RedirectStandardOutput = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $p = [Diagnostics.Process]::Start($psi)
    $read = $p.StandardOutput.ReadToEndAsync()
    if (-not $p.WaitForExit(900000)) { try { $p.Kill() } catch { }; Write-Host "APEX.exe $Action : délai de 900 s dépassé"; return 98 }
    $s = $null; if ($read.Wait(30000)) { try { $s = $read.Result | ConvertFrom-Json } catch { } }
    Write-Host "APEX.exe $Action : code $($p.ExitCode) ; $($s.headline) / $($s.cause)"
    if ($p.ExitCode -gt 1 -and $Action -eq '--start') { Write-Host "guidance : $($s.guidance)" }
    return $p.ExitCode
}

function Stop-Apex {
    $c = Panel '--stop'
    if ($c -ne 0) { Start-Sleep -Seconds 20; $c = Panel '--stop' }
    return $c
}

function Loop([string]$Phase) {
    $wslArgs = @()
    if ($Distro) { $wslArgs += @('-d', $Distro) }
    $wslArgs += @('--cd', '~', '--exec', "$ApexPathInWsl/.venv/bin/python",
        "$ApexPathInWsl/setup/packaging/windows/ci/first_real_loop.py", $Phase)
    $env:WSL_UTF8 = '1'
    & wsl.exe @wslArgs | Out-Host
    return $LASTEXITCODE
}

Write-Host "PREUVE MACHINE CIBLE — $(Get-Date -Format o) — $env:COMPUTERNAME"
$codes = @()
if ((Panel '--start') -gt 1) { Write-Host 'BLOCKED : APEX ne démarre pas (voir le verdict ci-dessus)'; Stop-Transcript | Out-Null; exit 3 }
$codes += Loop 'run'
Stop-Apex | Out-Null
if ((Panel '--start') -gt 1) { Write-Host 'FAIL : APEX ne redémarre pas'; Stop-Transcript | Out-Null; exit 1 }
$codes += Loop 'verify'
$codes += Loop 'interrupt'
Stop-Apex | Out-Null
if ((Panel '--start') -gt 1) { Write-Host 'FAIL : APEX ne redémarre pas'; Stop-Transcript | Out-Null; exit 1 }
$codes += Loop 'interrupted'
Write-Host "codes des phases (run, verify, interrupt, interrupted) : $($codes -join ', ') (0 = preuves vérifiées, 1 = preuve contredite, 3 = BLOCKED)"
Write-Host "APEX reste démarré. Journal : $OutDir\journal.txt"
Stop-Transcript | Out-Null
if ($codes -contains 1) { exit 1 }
if ($codes -contains 3) { exit 3 }
exit 0
