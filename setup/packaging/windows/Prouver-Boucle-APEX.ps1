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

  -WithExperience : enchaîne ensuite l'expérience RUN 1 -> RUN 2 pré-enregistrée (docs/EXPERIENCE_RUN1_RUN2.md,
  scripts/apex_experience_ab.py) avec le modèle réellement installé ; résultats copiés dans le dossier du journal.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Prouver-Boucle-APEX.ps1 -AllowRestart
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File Prouver-Boucle-APEX.ps1 -AllowRestart -WithExperience -Replicates 4
#>
param(
    [switch]$AllowRestart,
    [switch]$WithExperience,
    [int]$Replicates = 4,
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

function Wsl-Exec([string[]]$Command) {
    $wslArgs = @()
    if ($Distro) { $wslArgs += @('-d', $Distro) }
    $wslArgs += @('--cd', '~', '--exec') + $Command
    $env:WSL_UTF8 = '1'
    & wsl.exe @wslArgs
}

function Machine-Facts {
    # Relevé de la machine RÉELLE : rien n'est supposé, une mesure absente est dite absente.
    $cpu = (Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1).Name
    $ram = (Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).TotalPhysicalMemory
    Write-Host "Windows : $([Environment]::OSVersion.VersionString) ; CPU : $cpu ; RAM : $([math]::Round($ram / 1GB, 1)) Go"
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($smi) { Write-Host ('GPU : ' + (& $smi.Source --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader)) }
    else { Write-Host 'GPU : nvidia-smi introuvable côté Windows (non mesuré)' }
    Write-Host ('APEX (dépôt WSL) : commit ' + ((Wsl-Exec @('git', '-C', $ApexPathInWsl, 'rev-parse', '--short', 'HEAD')) -join ''))
    $models = (Wsl-Exec @('ollama', 'list')) -join "`n"
    Write-Host "Modèles Ollama installés :`n$models"
    if ($models -notmatch 'qwen2\.5-coder:7b-instruct') {
        Write-Host 'ATTENTION : qwen2.5-coder:7b-instruct absent de ce Ollama — le modèle de référence ne sera PAS celui mesuré.'
    }
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
Machine-Facts
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
if ($WithExperience) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $wslOut = "apex-experience-ab-$stamp"
    Write-Host "EXPÉRIENCE RUN 1 -> RUN 2 ($Replicates répliques par tâche ; résultats WSL : ~/$wslOut)"
    $env:WSL_UTF8 = '1'
    Wsl-Exec @("$ApexPathInWsl/.venv/bin/python", "$ApexPathInWsl/scripts/apex_experience_ab.py",
        '--replicates', [string]$Replicates, '--out', $wslOut) | Out-Host
    $codes += $LASTEXITCODE
    foreach ($f in 'runs.jsonl', 'summary.json') {
        (Wsl-Exec @('cat', "$wslOut/$f")) -join "`n" | Set-Content -Path (Join-Path $OutDir $f) -Encoding UTF8
    }
}
Write-Host "codes des phases (run, verify, interrupt, interrupted[, experience]) : $($codes -join ', ') (0 = preuves vérifiées / expérience menée à terme, 1 = preuve contredite, 3 = BLOCKED)"
Write-Host "APEX reste démarré. Journal : $OutDir\journal.txt"
Stop-Transcript | Out-Null
if ($codes -contains 1) { exit 1 }
if ($codes -contains 3) { exit 3 }
exit 0
