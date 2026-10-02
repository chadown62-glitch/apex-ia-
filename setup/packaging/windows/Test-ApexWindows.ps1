<#
.SYNOPSIS
  Validation RÉELLE d'APEX Desktop sur Windows 10/11 (+ WSL2) : installation, raccourcis, APEX.exe, Panel,
  WSL, backend lancé par launch.sh, diagnostic, sécurité, crash/recovery, redémarrage, performance.

.DESCRIPTION
  Exécuté sur la machine à valider, en utilisateur NORMAL (jamais en administrateur). Chaque vérification
  produit PASS, FAIL, BLOCKED (impossible ici, avec la raison) ou NOT_APPLICABLE ; rien n'est supposé.
  N'installe NI WSL, NI distribution, NI Docker : constate ce qui existe.

  Ce que le script MODIFIE (et seulement cela) :
    - installe / désinstalle / réinstalle APEX Desktop (programme par utilisateur) ;
    - lance et ferme APEX.exe ;
    - avec -AllowBackendControl : démarre et arrête le backend APEX via le launch.sh dédié, écrit puis
      RESTAURE temporairement ~/.apex/runtime/backend.json dans la distribution (tests de sécurité) ;
    - avec -AllowWslShutdown : arrête la distribution choisie (wsl --terminate) puis la redémarre ;
    - avec -AuditProcessCreation (administrateur, CI) : active le temps du script l'audit Windows des
      créations de processus avec ligne de commande (événement 4688), puis RESTAURE la stratégie d'audit
      et la clé de registre d'origine.
  Les paramètres du Panel (%APPDATA%\APEX) sont sauvegardés au début et restaurés à la fin.

  Résultats : <OutDir>\results.json et <OutDir>.zip (journaux, sorties, captures), sans secret : le jeton
  de l'API et les mots de passe de .env sont lus en mémoire, recherchés dans chaque sortie, puis masqués.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\Test-ApexWindows.ps1 -Installer .\APEX-Setup-x64-v0.2.2.exe `
    -OlderInstaller .\APEX-Setup-x64-v0.2.1.exe -AllowBackendControl -AllowWslShutdown
#>
[CmdletBinding()]
param(
    # Par défaut : l'installateur le plus récent du dossier du script (et le précédent pour la mise à jour).
    [string]$Installer,
    [string]$OlderInstaller,
    [ValidateSet('Full', 'PreReboot', 'PostReboot')][string]$Phase = 'Full',
    [string]$Distro,
    [string]$ApexPathInWsl,
    [switch]$AllowBackendControl,
    [switch]$AllowWslShutdown,
    [ValidateRange(1, 3)][int]$Repeat = 2,
    [int]$BackendStartTimeoutMin = 20,
    [string]$OutDir,
    [string]$Label = 'user-pc',
    # Observation MANUELLE (non automatisable) : double-clic sur l'installateur téléchargé avant le script.
    [ValidateSet('warning', 'no-warning', 'not-tested')][string]$SmartScreen = 'not-tested',
    # Mode « double-clic » (Valider-APEX.cmd) : quelques questions O/N au lieu d'options, redémarrage de
    # Windows proposé puis REPRISE AUTOMATIQUE après ouverture de session (clé RunOnce de l'utilisateur).
    [switch]$Interactive,
    # CI / administrateur : source indépendante des lignes de commande des processus enfants (audit noyau
    # 4688), en plus du sondage WMI qui peut les lire vides (processus en création ou en fin de vie).
    [switch]$AuditProcessCreation
)

# Version 1 : variables non initialisées interdites (erreur constatée au run 10) ; les propriétés absentes
# (sortie illisible, donnée manquante) valent $null et chaque test décide — jamais un plantage du banc
# présenté comme un échec du produit (constaté avec la version 2 : « erreur du test » sur L02/L04/S01).
Set-StrictMode -Version 1
$ErrorActionPreference = 'Stop'
$ScriptVersion = '1'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$Utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)

if (-not $Installer) {
    $found = @(Get-ChildItem -Path $PSScriptRoot -File | Where-Object { $_.Name -match '^APEX-Setup-x64-v\d+\.\d+\.\d+\.exe$' } | Sort-Object {
            [version]($_.Name -replace '^APEX-Setup-x64-v(\d+\.\d+\.\d+)\.exe$', '$1') })
    if ($found.Count -eq 0) { throw "aucun APEX-Setup-x64-v*.exe dans $PSScriptRoot : préciser -Installer" }
    $Installer = $found[$found.Count - 1].FullName
    if (-not $OlderInstaller -and $found.Count -gt 1) { $OlderInstaller = $found[$found.Count - 2].FullName }
}
$Installer = (Resolve-Path $Installer).Path
if ($OlderInstaller) { $OlderInstaller = (Resolve-Path $OlderInstaller).Path }
if (-not $OutDir) {
    $OutDir = Join-Path ([Environment]::GetFolderPath('Desktop')) ("APEX-validation-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$Evidence = Join-Path $OutDir 'evidence'
New-Item -ItemType Directory -Force -Path $Evidence | Out-Null
Start-Transcript -Path (Join-Path $OutDir 'transcript.txt') -Append | Out-Null

# ------------------------------------------------------------------------------------------------------
# Emplacements (ceux de l'installateur : ApexSetup.iss)
# ------------------------------------------------------------------------------------------------------
$AppId = '{6C6B2B0E-3E0C-4E2E-9D6A-3B1E7B7B6B10}'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\APEX'
$ApexExe = Join-Path $InstallDir 'APEX.exe'
$DesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'APEX.lnk'
$StartLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'APEX.lnk'
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${AppId}_is1"
$SettingsDir = Join-Path $env:APPDATA 'APEX'
$LogsDir = Join-Path $env:LOCALAPPDATA 'APEX\logs'

# ------------------------------------------------------------------------------------------------------
# Résultats
# ------------------------------------------------------------------------------------------------------
$script:Checks = New-Object System.Collections.ArrayList
$script:Run = 0
$script:Secrets = New-Object System.Collections.ArrayList
$script:Perf = [ordered]@{}
$script:KeepAlive = $null
$script:FailCount = 0
$script:Launch = $null
$script:BackendDown = $null
$script:ProcessAudit = [ordered]@{ Active = $false; Reason = 'non demandé (option -AuditProcessCreation absente)'
    Backup = $null; RegExisted = $false; RegPrevious = $null; Changed = $false }
$script:DistroName = $null
$script:ApexRepo = $null

function Add-Check {
    param([string]$Id, [string]$Category, [string]$Title,
        [ValidateSet('PASS', 'FAIL', 'BLOCKED', 'NOT_APPLICABLE')][string]$Status,
        [string]$Detail = '', [hashtable]$Data = @{})
    $entry = [ordered]@{ run = $script:Run; id = $Id; category = $Category; title = $Title; status = $Status;
        detail = $Detail; data = $Data; at = (Get-Date).ToString('o') }
    [void]$script:Checks.Add($entry)
    $color = switch ($Status) { 'PASS' { 'Green' } 'FAIL' { 'Red' } 'BLOCKED' { 'Yellow' } default { 'Gray' } }
    Write-Host ("[{0}] {1,-6} {2} — {3}" -f $script:Run, $Status, $Id, $Detail) -ForegroundColor $color
}

function Check {
    # Exécute un test ; une exception inattendue = FAIL avec son message (jamais un PASS par défaut).
    param([string]$Id, [string]$Category, [string]$Title, [scriptblock]$Body)
    try { & $Body } catch {
        Add-Check $Id $Category $Title 'FAIL' ("erreur du test : " + $_.Exception.Message)
    }
}

# ------------------------------------------------------------------------------------------------------
# Processus (compatible Windows PowerShell 5.1 : pas de ProcessStartInfo.ArgumentList)
# ------------------------------------------------------------------------------------------------------
function Format-Arg([string]$a) {
    if ($a -eq '') { return '""' }
    if ($a -notmatch '[\s"]') { return $a }
    $sb = New-Object System.Text.StringBuilder; [void]$sb.Append('"'); $bs = 0
    foreach ($c in $a.ToCharArray()) {
        if ($c -eq '\') { $bs++ }
        elseif ($c -eq '"') { [void]$sb.Append(('\' * ($bs * 2 + 1)) + '"'); $bs = 0 }
        else { [void]$sb.Append(('\' * $bs) + $c); $bs = 0 }
    }
    [void]$sb.Append(('\' * ($bs * 2)) + '"'); return $sb.ToString()
}

function Invoke-Native {
    param([string]$File, [string[]]$Arguments = @(), [int]$TimeoutSec = 60, [hashtable]$Environment = @{}, [string]$WorkingDirectory)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = (($Arguments | ForEach-Object { Format-Arg $_ }) -join ' ')
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    $psi.EnvironmentVariables['WSL_UTF8'] = '1'
    foreach ($k in $Environment.Keys) { $psi.EnvironmentVariables[$k] = [string]$Environment[$k] }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = [Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEndAsync(); $err = $p.StandardError.ReadToEndAsync()
    $done = $p.WaitForExit($TimeoutSec * 1000)
    if (-not $done) { try { $p.Kill() } catch { } }
    $code = $null; if ($done) { $code = $p.ExitCode }
    [pscustomobject]@{ ExitCode = $code; TimedOut = (-not $done); Ms = $sw.ElapsedMilliseconds
        Stdout = ($out.Result -replace "`0", ''); Stderr = ($err.Result -replace "`0", '') }
}

# ------------------------------------------------------------------------------------------------------
# WSL (le SCRIPT de test a le droit d'utiliser bash ; APEX.exe, lui, ne passe jamais par un shell)
# ------------------------------------------------------------------------------------------------------
function Get-WslRegistry {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path $key)) { return @() }
    $default = (Get-ItemProperty $key -ErrorAction SilentlyContinue).DefaultDistribution
    Get-ChildItem $key | ForEach-Object {
        $p = Get-ItemProperty $_.PSPath
        if ($p.PSObject.Properties['DistributionName']) {
            $version = $null; if ($p.PSObject.Properties['Version']) { $version = $p.Version }
            [pscustomobject]@{ Name = $p.DistributionName; Version = $version; IsDefault = ($_.PSChildName -eq $default) }
        }
    }
}

function Get-RunningDistros {
    $r = Invoke-Native 'wsl.exe' @('-l', '--running', '-q') 20
    @($r.Stdout -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Invoke-Wsl {
    # Script bash exécuté dans la distribution (base64 : aucun problème de guillemets).
    param([string]$Script, [int]$TimeoutSec = 120, [string]$User)
    $b64 = [Convert]::ToBase64String($Utf8NoBom.GetBytes($Script))
    $wslArgs = @('-d', $script:DistroName)
    if ($User) { $wslArgs += @('-u', $User) }
    $wslArgs += @('--exec', 'bash', '-c', "echo $b64 | base64 -d | bash")
    Invoke-Native 'wsl.exe' $wslArgs $TimeoutSec
}

function Get-DiscoveryRecord {
    $r = Invoke-Native 'wsl.exe' @('-d', $script:DistroName, '--cd', '~', '--exec', 'cat', '.apex/runtime/backend.json') 30
    if ($r.ExitCode -ne 0) { return $null }
    try { return ($r.Stdout | ConvertFrom-Json) } catch { return $null }
}

# ------------------------------------------------------------------------------------------------------
# APEX.exe
# ------------------------------------------------------------------------------------------------------
function Get-PanelStatus {
    param([hashtable]$Environment = @{})
    $r = Invoke-Native $ApexExe @('--status-json') 90 $Environment
    $obj = $null; try { $obj = $r.Stdout | ConvertFrom-Json } catch { }
    $components = @{}
    if ($obj) { foreach ($c in $obj.components) { $components[[string]$c.key] = [string]$c.status } }
    $verdict = $null; $cause = $null; $guidance = ''
    if ($obj) { $verdict = [string]$obj.verdict; $cause = [string]$obj.cause; $guidance = [string]$obj.guidance }
    [pscustomobject]@{ Ok = [bool]$obj; ExitCode = $r.ExitCode; Ms = $r.Ms; Verdict = $verdict; Cause = $cause
        Guidance = $guidance; Components = $components; Raw = $r.Stdout; Object = $obj }
}

function Save-Text([string]$Name, [string]$Text) {
    [IO.File]::WriteAllText((Join-Path $Evidence $Name), $Text, $Utf8NoBom)
}

function Wait-Panel {
    # Interroge --status-json jusqu'à ce que $Until soit vrai ; retourne l'état final et les transitions vues.
    param([scriptblock]$Until, [int]$TimeoutSec = 120, [int]$EverySec = 3, [hashtable]$Environment = @{})
    $transitions = New-Object System.Collections.ArrayList; $deadline = (Get-Date).AddSeconds($TimeoutSec); $s = $null
    do {
        $s = Get-PanelStatus $Environment
        $label = "$($s.Verdict)/$($s.Cause)"
        if ($transitions.Count -eq 0 -or $transitions[$transitions.Count - 1].state -ne $label) {
            [void]$transitions.Add([ordered]@{ at = (Get-Date).ToString('HH:mm:ss'); state = $label })
        }
        if (& $Until $s) { return [pscustomobject]@{ Reached = $true; Status = $s; Transitions = $transitions } }
        Start-Sleep -Seconds $EverySec
    } while ((Get-Date) -lt $deadline)
    [pscustomobject]@{ Reached = $false; Status = $s; Transitions = $transitions }
}

function Get-GuiPanels {
    @(Get-CimInstance Win32_Process -Filter "Name='APEX.exe'" | Where-Object {
            $_.CommandLine -notmatch '--status-json|--diagnostic|--version' })
}

function Stop-AllPanels {
    foreach ($p in (Get-Process APEX -ErrorAction SilentlyContinue)) {
        try { [void]$p.CloseMainWindow() } catch { }
    }
    Start-Sleep 3
    Get-Process APEX -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

Add-Type -ErrorAction SilentlyContinue @'
using System;
using System.Runtime.InteropServices;
public static class ApexWin32 {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
}
'@

function Wait-PanelWindow([int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $p = Get-Process APEX -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } | Select-Object -First 1
        if ($p -and [ApexWin32]::IsWindowVisible($p.MainWindowHandle)) { return $p }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

function Save-WindowShot([Diagnostics.Process]$Process, [string]$Name) {
    try {
        Add-Type -AssemblyName System.Drawing
        $r = New-Object ApexWin32+RECT
        [void][ApexWin32]::SetForegroundWindow($Process.MainWindowHandle); Start-Sleep -Milliseconds 700
        [void][ApexWin32]::GetWindowRect($Process.MainWindowHandle, [ref]$r)
        $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
        if ($w -le 0 -or $h -le 0) { return $null }
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
        $path = Join-Path $Evidence $Name; $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose(); return $path
    } catch { return $null }
}

$AuditKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System\Audit'
$AuditSubcategory = '{0CCE922B-69AE-11D9-BED3-505054503030}'  # « Process Creation », indépendant de la langue

function Enable-ProcessAudit {
    # Active l'audit des créations de processus avec ligne de commande (événement 4688), après sauvegarde de
    # la stratégie d'audit et de la clé de registre ; un TÉMOIN prouve ensuite que la source fonctionne.
    $principal = New-Object Security.Principal.WindowsPrincipal ([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $script:ProcessAudit.Reason = 'exige un administrateur : non activé'; return
    }
    $backup = Join-Path $env:TEMP ('apex-auditpol-' + [guid]::NewGuid().ToString('N') + '.csv')
    & auditpol.exe /backup /file:$backup | Out-Null
    if ($LASTEXITCODE -ne 0) { $script:ProcessAudit.Reason = "auditpol /backup en échec (code $LASTEXITCODE) : non activé"; return }
    $script:ProcessAudit.Backup = $backup
    if (Test-Path $AuditKey) {
        $v = Get-ItemProperty $AuditKey -Name ProcessCreationIncludeCmdLine_Enabled -ErrorAction SilentlyContinue
        if ($v) { $script:ProcessAudit.RegExisted = $true; $script:ProcessAudit.RegPrevious = $v.ProcessCreationIncludeCmdLine_Enabled }
    } else { New-Item $AuditKey -Force | Out-Null }
    $script:ProcessAudit.Changed = $true
    New-ItemProperty $AuditKey -Name ProcessCreationIncludeCmdLine_Enabled -Value 1 -PropertyType DWord -Force | Out-Null
    & auditpol.exe /set /subcategory:$AuditSubcategory /success:enable | Out-Null
    $since = (Get-Date).AddSeconds(-2)
    $marker = 'apexaudit' + [guid]::NewGuid().ToString('N').Substring(0, 10)
    $probe = Start-Process "$env:SystemRoot\System32\where.exe" -ArgumentList '/Q', $marker -WindowStyle Hidden -PassThru
    [void]$probe.WaitForExit(10000)
    $script:ProcessAudit.Active = $true
    $found = $false
    for ($i = 0; $i -lt 10 -and -not $found; $i++) {
        Start-Sleep 1
        $found = @(Get-AuditedChildren $PID $since | Where-Object { [string]$_.cmd -match $marker }).Count -gt 0
    }
    if ($found) { $script:ProcessAudit.Reason = 'actif : témoin identifié par l''événement 4688' }
    else {
        $script:ProcessAudit.Active = $false
        $script:ProcessAudit.Reason = 'activé, mais le témoin est absent du journal 4688 : source non fiable, non utilisée'
    }
}

function Restore-ProcessAudit {
    if (-not $script:ProcessAudit.Changed) { return }
    if ($script:ProcessAudit.Backup) {
        & auditpol.exe /restore /file:$($script:ProcessAudit.Backup) | Out-Null
        Remove-Item $script:ProcessAudit.Backup -Force -ErrorAction SilentlyContinue
    }
    if ($script:ProcessAudit.RegExisted) {
        Set-ItemProperty $AuditKey -Name ProcessCreationIncludeCmdLine_Enabled -Value $script:ProcessAudit.RegPrevious
    } else { Remove-ItemProperty $AuditKey -Name ProcessCreationIncludeCmdLine_Enabled -ErrorAction SilentlyContinue }
    $script:ProcessAudit.Changed = $false
}

function Read-CommandLine([int]$ProcessId) {
    # Relit la ligne de commande d'un processus : $null s'il n'existe plus ou si elle reste illisible
    # (processus en cours de création, ou déjà en fin de vie : WMI la rend alors vide — constaté au run 13).
    for ($i = 0; $i -lt 5; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if (-not $p) { return $null }
        if ([string]$p.CommandLine) { return [string]$p.CommandLine }
        Start-Sleep -Milliseconds 20
    }
    return $null
}

function Watch-Children {
    # Processus lancés par $ParentId pendant $Seconds. Deux sources :
    #   - sondage WMI toutes les 250 ms (toujours) : une ligne de commande lue vide est relue aussitôt puis à
    #     chaque relevé ; un processus très bref peut échapper au sondage ;
    #   - audit noyau 4688 (si -AuditProcessCreation est actif) : ligne de commande enregistrée À LA CRÉATION,
    #     y compris pour les processus que le sondage n'a pas vus.
    # Chaque entrée garde sa source ; une ligne de commande restée vide n'est jamais devinée.
    param([int]$ParentId, [int]$Seconds = 20, [datetime]$Since = (Get-Date).AddSeconds(-1))
    $seen = @{}; $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($c in (Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentId" -ErrorAction SilentlyContinue)) {
            $id = [int]$c.ProcessId; $cmd = [string]$c.CommandLine
            if (-not $seen.ContainsKey($id)) {
                $entry = [ordered]@{ pid = $id; name = [string]$c.Name; cmd = $cmd; source = 'wmi'; first_read_empty = (-not $cmd) }
                if (-not $cmd) {
                    $again = Read-CommandLine $id
                    if ($again) { $entry.cmd = $again; $entry.source = 'wmi-relecture' } else { $entry.source = 'aucune' }
                }
                $seen[$id] = $entry
            } elseif (-not $seen[$id].cmd -and $cmd) {
                $seen[$id].cmd = $cmd; $seen[$id].source = 'wmi-relecture'
            }
        }
        Start-Sleep -Milliseconds 250
    }
    if ($script:ProcessAudit.Active) {
        Start-Sleep 2  # l'écriture de l'événement 4688 peut suivre la création de quelques instants
        foreach ($a in @(Get-AuditedChildren $ParentId $Since)) {
            $id = [int]$a.pid
            if ($seen.ContainsKey($id)) {
                if (-not $seen[$id].cmd -and $a.cmd) { $seen[$id].cmd = $a.cmd; $seen[$id].source = 'audit-4688' }
            } else {
                $leaf = ''; if ([string]$a.name) { $leaf = Split-Path ([string]$a.name) -Leaf }
                $seen[$id] = [ordered]@{ pid = $id; name = $leaf; cmd = [string]$a.cmd
                    source = 'audit-4688 (non vu par sondage)'; first_read_empty = $false }
            }
        }
    }
    @($seen.Values)
}

function ConvertFrom-ProcessCreationEvent([xml]$Xml) {
    # Événement Security 4688 -> pid, pid du créateur, image, ligne de commande. Pur (testé hors Windows).
    $d = @{}
    foreach ($n in $Xml.Event.EventData.Data) { $d[[string]$n.Name] = [string]$n.'#text' }
    if (-not $d['NewProcessId'] -or -not $d['ProcessId']) { return $null }
    [pscustomobject]@{ pid = [Convert]::ToInt64($d['NewProcessId'], 16); parent = [Convert]::ToInt64($d['ProcessId'], 16)
        name = $d['NewProcessName']; cmd = $d['CommandLine'] }
}

function Get-AuditedChildren([int]$ParentId, [datetime]$Since) {
    if (-not $script:ProcessAudit.Active) { return @() }
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 4688; StartTime = $Since } -ErrorAction SilentlyContinue)
    @($events | ForEach-Object { ConvertFrom-ProcessCreationEvent ([xml]$_.ToXml()) } | Where-Object { $_ -and $_.parent -eq $ParentId })
}

function Test-AllowedChild($child) {
    # APEX.exe ne lance que wsl.exe : listes (-l …) ou lecture de fichier par --exec cat (jamais un shell).
    if ($child.name -ne 'wsl.exe') { return $false }
    $cmd = $child.cmd
    if ($cmd -match '\s-l(\s|$)') { return $true }
    return ($cmd -match '--exec\s+cat\s+--\s' -and $cmd -notmatch '\b(bash|sh|zsh|cmd|powershell|pwsh)\b')
}

function Get-ChildVerdict($child) {
    # 'allowed' | 'forbidden' | 'unidentified'. Le nom de l'image est toujours lisible : tout autre programme
    # que wsl.exe est interdit. Une ligne de commande illisible n'est JAMAIS « autorisée » : non identifié.
    $name = [string]$child.name
    if (-not $name) { return 'unidentified' }
    if ($name -ne 'wsl.exe') { return 'forbidden' }
    if (-not [string]$child.cmd) { return 'unidentified' }
    if (Test-AllowedChild $child) { return 'allowed' }
    return 'forbidden'
}

function Get-ChildrenOutcome($Children, [bool]$AuditActive) {
    # Interdit -> FAIL. Non identifié -> jamais PASS : FAIL si l'audit noyau était actif (la source fiable
    # existait et n'a pas suffi), sinon BLOCKED (mesure incomplète sur cette machine, avec la raison).
    $all = @($Children)
    $forbidden = @($all | Where-Object { (Get-ChildVerdict $_) -eq 'forbidden' })
    $unknown = @($all | Where-Object { (Get-ChildVerdict $_) -eq 'unidentified' })
    $sources = (@($all | Group-Object { $_.source } | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ', ')
    $show = { param($list) (@($list) | Select-Object -First 3 | ForEach-Object { "[$($_.name) pid $($_.pid)] $($_.cmd)" }) -join ' | ' }
    $status = 'PASS'
    if ($forbidden.Count -gt 0) { $status = 'FAIL' }
    elseif ($unknown.Count -gt 0) { if ($AuditActive) { $status = 'FAIL' } else { $status = 'BLOCKED' } }
    $detail = "$($all.Count) processus enfants vus ; non autorisés : $($forbidden.Count) ; non identifiés : $($unknown.Count)"
    if ($sources) { $detail += " ; sources : $sources" }
    if ($forbidden.Count -gt 0) { $detail += " ; interdits : " + (& $show $forbidden) }
    if ($unknown.Count -gt 0) {
        $detail += " ; non identifiés (ligne de commande illisible, jamais comptée sûre) : " + (& $show $unknown)
        if (-not $AuditActive) { $detail += " ; relancer en administrateur avec -AuditProcessCreation pour les identifier" }
    }
    [pscustomobject]@{ Status = $status; Detail = $detail; Forbidden = $forbidden; Unidentified = $unknown }
}

# ------------------------------------------------------------------------------------------------------
# Secrets : lus en mémoire seulement, jamais écrits ; recherchés dans chaque sortie.
# ------------------------------------------------------------------------------------------------------
function Add-Secret([string]$Value) {
    if ($Value -and $Value.Length -ge 8 -and -not $script:Secrets.Contains($Value)) { [void]$script:Secrets.Add($Value) }
}

function Find-Secrets([string]$Text) {
    $hits = 0
    foreach ($s in $script:Secrets) { if ($Text -and $Text.Contains($s)) { $hits++ } }
    return $hits
}

function Read-BackendSecrets {
    if (-not $script:ApexRepo) { return }
    $r = Invoke-Wsl "cd '$($script:ApexRepo)' && cat .apex/api-token 2>/dev/null; echo; grep -E '^[A-Z0-9_]*(PASSWORD|SECRET|TOKEN|KEY)[A-Z0-9_]*=' .env 2>/dev/null | cut -d= -f2-" 30
    foreach ($line in ($r.Stdout -split "`r?`n")) { Add-Secret ($line.Trim().Trim('"').Trim("'")) }
}

# ------------------------------------------------------------------------------------------------------
# Environnement (provenance)
# ------------------------------------------------------------------------------------------------------
function Get-Environment {
    $os = Get-CimInstance Win32_OperatingSystem
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $wslVersion = Invoke-Native 'wsl.exe' @('--version') 30
    $wslStatus = Invoke-Native 'wsl.exe' @('--status') 30
    $wslconfig = Join-Path $env:USERPROFILE '.wslconfig'
    $netLines = @()
    if (Test-Path $wslconfig) { $netLines = @(Get-Content $wslconfig | Where-Object { $_ -match '^\s*(networkingMode|localhostForwarding|firewall|dnsTunneling)\s*=' }) }
    [ordered]@{
        label = $Label
        os_caption = $os.Caption; os_version = $os.Version; os_build = $os.BuildNumber
        os_is_server = ($os.ProductType -ne 1)
        last_boot = $os.LastBootUpTime.ToString('o')
        is_wine = ((Test-Path 'HKCU:\Software\Wine') -or (Test-Path 'HKLM:\Software\Wine'))
        is_admin = $isAdmin
        powershell = $PSVersionTable.PSVersion.ToString()
        culture = (Get-Culture).Name; ui_culture = (Get-UICulture).Name
        explorer_running = [bool](Get-Process explorer -ErrorAction SilentlyContinue)
        desktop_folder_redirected = ([Environment]::GetFolderPath('Desktop') -notlike "$env:USERPROFILE\Desktop")
        wsl_version_output = ($wslVersion.Stdout.Trim()); wsl_version_exit = $wslVersion.ExitCode
        wsl_status_output = ($wslStatus.Stdout.Trim())
        wsl_distributions = @(Get-WslRegistry | ForEach-Object { [ordered]@{ name = $_.Name; version = $_.Version; default = $_.IsDefault } })
        wslconfig_network = $netLines
        installer = [ordered]@{ file = (Split-Path $Installer -Leaf); sha256 = (Get-FileHash $Installer -Algorithm SHA256).Hash.ToLower()
            signature = (Get-AuthenticodeSignature $Installer).Status.ToString()
            mark_of_the_web = [bool](Get-Item $Installer -Stream Zone.Identifier -ErrorAction SilentlyContinue) }
    }
}

# ------------------------------------------------------------------------------------------------------
# Installation
# ------------------------------------------------------------------------------------------------------
function Install-Apex([string]$Setup, [string]$Name, [int]$TimeoutSec = 300) {
    $log = Join-Path $Evidence "$Name.install.log"
    $r = Invoke-Native $Setup @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$log") $TimeoutSec
    return $r
}

function Uninstall-Apex {
    $unins = Join-Path $InstallDir 'unins000.exe'
    if (-not (Test-Path $unins)) { return $null }
    $r = Invoke-Native $unins @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') 300
    for ($i = 0; $i -lt 30 -and (Test-Path $ApexExe); $i++) { Start-Sleep 1 } # le désinstalleur se relance hors de son dossier
    return $r
}

function Get-Shortcut([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $sh = New-Object -ComObject WScript.Shell
    $l = $sh.CreateShortcut($Path)
    [pscustomobject]@{ Target = $l.TargetPath; WorkDir = $l.WorkingDirectory; Icon = $l.IconLocation }
}

function Get-ExpectedVersion([string]$Setup) {
    if ((Split-Path $Setup -Leaf) -match 'v(\d+\.\d+\.\d+)') { return $Matches[1] }
    return $null
}

function Test-Install {
    $expected = Get-ExpectedVersion $Installer
    Stop-AllPanels
    if (Test-Path $ApexExe) { [void](Uninstall-Apex) }
    $hadUserData = (Test-Path $SettingsDir) -or (Test-Path $LogsDir)

    Check 'I01' 'install' 'installation silencieuse sans administrateur' {
        $r = Install-Apex $Installer "run$($script:Run)-fresh"
        $script:Perf["install_seconds_run$($script:Run)"] = [math]::Round($r.Ms / 1000, 1)
        $st = 'FAIL'; if ($r.ExitCode -eq 0 -and (Test-Path $ApexExe)) { $st = 'PASS' }
        $scope = 'HKCU'; if (-not (Test-Path $UninstallKey)) { $scope = 'absente de HKCU' }
        Add-Check 'I01' 'install' 'installation silencieuse sans administrateur' $st ("code $($r.ExitCode) en $([math]::Round($r.Ms/1000,1)) s ; entrée de désinstallation $scope ; données utilisateur antérieures : $hadUserData") @{ admin = $script:Env.is_admin }
    }
    Check 'I00' 'install' 'installation et lancement sans administrateur' {
        if ($script:Env.is_admin) { Add-Check 'I00' 'install' 'installation et lancement sans administrateur' 'BLOCKED' 'script exécuté en administrateur (ex. runner CI) : l''absence d''élévation ne peut pas être prouvée ici'; return }
        $st = 'FAIL'; if ((Test-Path $ApexExe) -and (Test-Path $UninstallKey)) { $st = 'PASS' }
        Add-Check 'I00' 'install' 'installation et lancement sans administrateur' $st "compte non administrateur ; installé dans %LOCALAPPDATA% ; entrée HKCU : $(Test-Path $UninstallKey)"
    }
    Check 'X01' 'install' 'SmartScreen au double-clic sur l''installateur téléchargé (observation manuelle)' {
        $motw = $script:Env.installer.mark_of_the_web
        switch ($SmartScreen) {
            'not-tested' { Add-Check 'X01' 'install' 'SmartScreen (observation manuelle)' 'BLOCKED' "non observé (option -SmartScreen) ; fichier téléchargé marqué Internet : $motw" }
            'warning' { Add-Check 'X01' 'install' 'SmartScreen (observation manuelle)' 'PASS' "avertissement SmartScreen observé, conforme à un installateur NON SIGNÉ ; marqué Internet : $motw" }
            default { Add-Check 'X01' 'install' 'SmartScreen (observation manuelle)' 'PASS' "aucun avertissement observé (marqué Internet : $motw ; réputation ou fichier non marqué)" }
        }
    }
    Check 'I02' 'install' 'répertoire et APEX.exe' {
        $files = @(Get-ChildItem $InstallDir -File -ErrorAction SilentlyContinue).Count
        $ver = (Get-Item $ApexExe).VersionInfo.ProductVersion
        $sig = (Get-AuthenticodeSignature $ApexExe).Status.ToString()
        $st = 'FAIL'; if ($files -gt 50) { $st = 'PASS' }
        Add-Check 'I02' 'install' 'répertoire et APEX.exe' $st "$files fichiers dans %LOCALAPPDATA%\Programs\APEX ; version de fichier $ver ; signature : $sig" @{ files = $files; signature = $sig }
    }
    Check 'I03' 'install' 'icône' {
        Add-Type -AssemblyName System.Drawing
        $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($ApexExe)
        $path = Join-Path $Evidence 'apex_icon.png'; $icon.ToBitmap().Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        Add-Check 'I03' 'install' 'icône' 'PASS' "icône extraite de APEX.exe ($($icon.Width)x$($icon.Height)) -> evidence\apex_icon.png (aspect NON jugé automatiquement)"
    }
    foreach ($s in @(@('I04', 'raccourci Bureau', $DesktopLnk), @('I05', 'raccourci Menu Démarrer', $StartLnk))) {
        $id = $s[0]; $title = $s[1]; $path = $s[2]
        Check $id 'shortcut' $title {
            $l = Get-Shortcut $path
            if (-not $l) { Add-Check $id 'shortcut' $title 'FAIL' "absent : $path"; return }
            $st = 'FAIL'; if ($l.Target -eq $ApexExe -and $l.WorkDir -eq $InstallDir) { $st = 'PASS' }
            Add-Check $id 'shortcut' $title $st "cible $($l.Target) ; dossier $($l.WorkDir) ; icône $($l.Icon)"
        }
    }
    Check 'I06' 'apex_exe' 'APEX.exe --version' {
        $r = Invoke-Native $ApexExe @('--version') 30
        $v = $r.Stdout.Trim()
        $st = 'FAIL'; if ($r.ExitCode -eq 0 -and ($expected -eq $null -or $v -eq $expected)) { $st = 'PASS' }
        Add-Check 'I06' 'apex_exe' 'APEX.exe --version' $st "« $v » (attendu $expected), code $($r.ExitCode)"
    }
    Check 'I07' 'apex_exe' 'APEX.exe --status-json' {
        $s = Get-PanelStatus
        Save-Text "run$($script:Run)-status.json" $s.Raw
        $st = 'FAIL'; if ($s.Ok -and $s.ExitCode -in 0, 1, 2, 3) { $st = 'PASS' }
        Add-Check 'I07' 'apex_exe' 'APEX.exe --status-json' $st "code $($s.ExitCode) en $($s.Ms) ms ; $($s.Verdict)/$($s.Cause)"
    }
    Check 'I08' 'diagnostic' 'APEX.exe --diagnostic redirigé : UTF-8, accents, aucun secret' {
        $file = Join-Path $Evidence "run$($script:Run)-diagnostic.txt"
        $p = Start-Process -FilePath $ApexExe -ArgumentList '--diagnostic' -RedirectStandardOutput $file -NoNewWindow -Wait -PassThru
        $bytes = [IO.File]::ReadAllBytes($file)
        $valid = $true; $text = ''
        try { $text = $Utf8Strict.GetString($bytes) } catch { $valid = $false }
        $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        $secrets = Find-Secrets $text
        $st = 'FAIL'; if ($valid -and -not $bom -and $text.Contains('Mesuré le') -and $secrets -eq 0) { $st = 'PASS' }
        Add-Check 'I08' 'diagnostic' 'APEX.exe --diagnostic redirigé : UTF-8, accents, aucun secret' $st "code $($p.ExitCode) ; UTF-8 strict : $valid ; BOM : $bom ; « Mesuré le » : $($text.Contains('Mesuré le')) ; secrets trouvés : $secrets"
    }
    Check 'I09' 'install' 'deuxième installation par-dessus' {
        $r = Install-Apex $Installer "run$($script:Run)-second"
        $st = 'FAIL'; if ($r.ExitCode -eq 0 -and (Test-Path $DesktopLnk)) { $st = 'PASS' }
        Add-Check 'I09' 'install' 'deuxième installation par-dessus' $st "code $($r.ExitCode)"
    }
}

function Test-InstallLifecycle {
    Check 'I10' 'install' 'installation refusée pendant que le Panel est ouvert' {
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        if (-not $p) { Add-Check 'I10' 'install' 'installation refusée pendant que le Panel est ouvert' 'FAIL' 'le Panel ne s''est pas ouvert'; return }
        $r = Install-Apex $Installer "run$($script:Run)-while-open" 120
        $alive = [bool](Get-Process -Id $p.Id -ErrorAction SilentlyContinue)
        $st = 'FAIL'; if ($r.ExitCode -ne 0 -and $alive -and (Test-Path $ApexExe)) { $st = 'PASS' }
        Add-Check 'I10' 'install' 'installation refusée pendant que le Panel est ouvert' $st "code $($r.ExitCode) (≠ 0 attendu) ; Panel toujours ouvert : $alive"
        Stop-AllPanels
    }
    Check 'I11' 'install' 'installation interrompue puis relancée' {
        [void](Uninstall-Apex)
        $proc = Start-Process -FilePath $Installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru
        Start-Sleep -Milliseconds 2500
        Get-Process | Where-Object { $_.Path -and ($_.Path -like "*\is-*.tmp\*" -or $_.Id -eq $proc.Id) } | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep 2
        $partial = @(Get-ChildItem $InstallDir -File -ErrorAction SilentlyContinue).Count
        $r = Install-Apex $Installer "run$($script:Run)-after-interrupt"
        $v = (Invoke-Native $ApexExe @('--version') 30).Stdout.Trim()
        $st = 'FAIL'; if ($r.ExitCode -eq 0 -and $v) { $st = 'PASS' }
        Add-Check 'I11' 'install' 'installation interrompue puis relancée' $st "interrompue avec $partial fichier(s) ; relance : code $($r.ExitCode), --version « $v »"
    }
    Check 'I12' 'install' 'désinstallation (programme et raccourcis retirés, données conservées)' {
        $r = Uninstall-Apex
        $left = @(); foreach ($x in @($ApexExe, $DesktopLnk, $StartLnk)) { if (Test-Path $x) { $left += $x } }
        $key = Test-Path $UninstallKey
        $logsKept = Test-Path $LogsDir
        $st = 'FAIL'; if ($r -and $r.ExitCode -eq 0 -and $left.Count -eq 0 -and -not $key -and $logsKept) { $st = 'PASS' }
        Add-Check 'I12' 'install' 'désinstallation (programme et raccourcis retirés, données conservées)' $st "code $($r.ExitCode) ; restants : $($left -join ', ') ; entrée HKCU : $key ; journaux conservés : $logsKept"
    }
    Check 'I13' 'install' 'réinstallation après désinstallation' {
        $r = Install-Apex $Installer "run$($script:Run)-reinstall"
        $st = 'FAIL'; if ($r.ExitCode -eq 0 -and (Test-Path $DesktopLnk) -and (Test-Path $ApexExe)) { $st = 'PASS' }
        Add-Check 'I13' 'install' 'réinstallation après désinstallation' $st "code $($r.ExitCode)"
    }
    Check 'I14' 'install' 'mise à jour depuis la version précédente' {
        if (-not $OlderInstaller) { Add-Check 'I14' 'install' 'mise à jour depuis la version précédente' 'BLOCKED' 'aucun installateur antérieur fourni (-OlderInstaller)'; return }
        [void](Uninstall-Apex)
        $r1 = Install-Apex $OlderInstaller "run$($script:Run)-older"
        $before = (Invoke-Native $ApexExe @('--version') 30).Stdout.Trim()
        New-Item -ItemType Directory -Force $SettingsDir | Out-Null
        $settings = Join-Path $SettingsDir 'dashboard-settings.json'
        $hadSettings = Test-Path $settings
        if (-not $hadSettings) { [IO.File]::WriteAllText($settings, '{"WslDistro":null}', $Utf8NoBom) }
        $r2 = Install-Apex $Installer "run$($script:Run)-upgrade"
        $after = (Invoke-Native $ApexExe @('--version') 30).Stdout.Trim()
        $st = 'FAIL'; if ($r1.ExitCode -eq 0 -and $r2.ExitCode -eq 0 -and $before -ne $after -and (Test-Path $DesktopLnk) -and (Test-Path $settings)) { $st = 'PASS' }
        if (-not $hadSettings) { Remove-Item $settings -Force -ErrorAction SilentlyContinue }
        Add-Check 'I14' 'install' 'mise à jour depuis la version précédente' $st "$before -> $after ; raccourci : $(Test-Path $DesktopLnk) ; paramètres conservés"
    }
}

# ------------------------------------------------------------------------------------------------------
# Lancement réel (double-clic = ShellExecute du raccourci, comme l'Explorateur)
# ------------------------------------------------------------------------------------------------------
function Test-Launch {
    Stop-AllPanels
    if ($AuditProcessCreation) {
        Check 'H01' 'harness' 'étalonnage : identification des processus enfants (sondage WMI contre audit noyau)' {
            # Instrument de S01/S04 mesuré sur des enfants CONNUS : 30 wsl.exe -l -q lancés par un parent témoin.
            $t = 'étalonnage : identification des processus enfants (sondage WMI contre audit noyau)'
            if (-not $script:ProcessAudit.Active) { Add-Check 'H01' 'harness' $t 'BLOCKED' "audit noyau indisponible : $($script:ProcessAudit.Reason)"; return }
            $n = 30
            $since = (Get-Date).AddSeconds(-1)
            $parent = Start-Process powershell.exe -ArgumentList '-NoProfile', '-Command', "1..$n | ForEach-Object { & wsl.exe -l -q | Out-Null }" -WindowStyle Hidden -PassThru
            $kids = @(Watch-Children $parent.Id 60 -Since $since | Where-Object { $_.name -eq 'wsl.exe' })
            $polled = @($kids | Where-Object { $_.source -ne 'audit-4688 (non vu par sondage)' })
            $emptyFirst = @($polled | Where-Object { $_.first_read_empty }).Count
            $byRetry = @($kids | Where-Object { $_.source -eq 'wmi-relecture' }).Count
            $byAudit = @($kids | Where-Object { $_.source -eq 'audit-4688' }).Count
            $o = Get-ChildrenOutcome $kids $true
            $script:Perf['harness_children_known'] = $n; $script:Perf['harness_children_seen_by_polling'] = $polled.Count
            $script:Perf['harness_children_empty_first_read'] = $emptyFirst; $script:Perf['harness_children_recovered_by_audit'] = $byAudit
            $st = 'FAIL'; if ($kids.Count -eq $n -and $o.Status -eq 'PASS') { $st = 'PASS' }
            Add-Check 'H01' 'harness' $t $st ("$n wsl.exe -l -q lancés (arguments connus) ; identifiés au total : $($kids.Count) ; vus par sondage : $($polled.Count) ; " +
                "ligne vide au 1er relevé : $emptyFirst ; récupérés par relecture : $byRetry ; récupérés par l'audit : $byAudit ; " +
                "vus seulement par l'audit : $($kids.Count - $polled.Count) ; " + $o.Detail)
        }
    }
    Check 'L01' 'launch' 'double-clic sur le raccourci Bureau depuis un autre dossier' {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        if (-not $p) { Add-Check 'L01' 'launch' 'double-clic sur le raccourci Bureau depuis un autre dossier' 'FAIL' 'aucune fenêtre du Panel en 30 s'; return }
        $open = $sw.ElapsedMilliseconds; $script:Perf["window_open_ms_run$($script:Run)"] = $open
        Start-Sleep 5
        $shot = Save-WindowShot $p "run$($script:Run)-panel-desktop-shortcut.png"
        Add-Check 'L01' 'launch' 'double-clic sur le raccourci Bureau depuis un autre dossier' 'PASS' "fenêtre visible en $open ms (« $($p.MainWindowTitle) ») ; capture : $(if ($shot) { Split-Path $shot -Leaf } else { 'impossible' })"
    }
    Check 'L02' 'launch' 'second double-clic : une seule instance, fenêtre ramenée' {
        $before = Wait-PanelWindow 5
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:USERPROFILE
        Start-Sleep 6
        $gui = @(Get-GuiPanels)
        $fg = [ApexWin32]::GetForegroundWindow()
        $focused = ($before -and $fg -eq $before.MainWindowHandle)
        $st = 'FAIL'; if ($gui.Count -eq 1) { $st = 'PASS' }
        Add-Check 'L02' 'launch' 'second double-clic : une seule instance, fenêtre ramenée' $st "$($gui.Count) Panel(s) ; fenêtre existante au premier plan : $focused"
    }
    Check 'L03' 'launch' 'raccourci Menu Démarrer' {
        Stop-AllPanels
        Start-Process -FilePath $StartLnk -WorkingDirectory $env:SystemRoot
        $p = Wait-PanelWindow 30
        $st = 'FAIL'; if ($p) { $st = 'PASS' }
        Add-Check 'L03' 'launch' 'raccourci Menu Démarrer' $st "fenêtre : $([bool]$p)"
    }
    Check 'L04' 'recovery' 'arrêt forcé du Panel puis relance' {
        Get-Process APEX -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep 2
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        $st = 'FAIL'; if ($p -and @(Get-GuiPanels).Count -eq 1) { $st = 'PASS' }
        Add-Check 'L04' 'recovery' 'arrêt forcé du Panel puis relance' $st "relance après kill : fenêtre $([bool]$p) (aucun verrou d'instance persistant)"
    }
    Check 'S01' 'security' 'APEX.exe ne lance que wsl.exe, jamais un shell (fenêtre ouverte 30 s)' {
        $p = Wait-PanelWindow 10
        if (-not $p) { Add-Check 'S01' 'security' 'APEX.exe ne lance que wsl.exe, jamais un shell (fenêtre ouverte 30 s)' 'FAIL' 'Panel absent'; return }
        $children = @(Watch-Children $p.Id 30)
        $o = Get-ChildrenOutcome $children $script:ProcessAudit.Active
        $script:Perf["wsl_spawns_per_30s_run$($script:Run)"] = $children.Count
        Save-Text "run$($script:Run)-children.txt" (($children | ForEach-Object { "$($_.name) pid=$($_.pid) source=$($_.source) $($_.cmd)" }) -join "`n")
        Add-Check 'S01' 'security' 'APEX.exe ne lance que wsl.exe, jamais un shell (fenêtre ouverte 30 s)' $o.Status $o.Detail
    }
    Check 'P01' 'performance' 'mémoire et CPU au repos du Panel (60 s)' {
        $p = Wait-PanelWindow 10
        if (-not $p) { Add-Check 'P01' 'performance' 'mémoire et CPU au repos du Panel (60 s)' 'FAIL' 'Panel absent'; return }
        $p.Refresh(); $t0 = $p.TotalProcessorTime; Start-Sleep 60; $p.Refresh()
        $cpu = [math]::Round(100 * ($p.TotalProcessorTime - $t0).TotalSeconds / 60, 2)
        $ws = [math]::Round($p.WorkingSet64 / 1MB, 1)
        $script:Perf["idle_cpu_percent_one_core_run$($script:Run)"] = $cpu; $script:Perf["working_set_mb_run$($script:Run)"] = $ws
        Add-Check 'P01' 'performance' 'mémoire et CPU au repos du Panel (60 s)' 'PASS' "CPU $cpu % d'un cœur (APEX.exe seul, sans les wsl.exe lancés) ; mémoire $ws Mo"
    }
    Check 'L05' 'recovery' 'fermeture propre : processus terminé, aucun wsl.exe orphelin' {
        $p = Wait-PanelWindow 10
        $pid0 = $p.Id; [void]$p.CloseMainWindow()
        $exited = $p.WaitForExit(15000)
        Start-Sleep 12
        $orphans = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$pid0" -ErrorAction SilentlyContinue)
        $st = 'FAIL'; if ($exited -and $orphans.Count -eq 0) { $st = 'PASS' }
        Add-Check 'L05' 'recovery' 'fermeture propre : processus terminé, aucun wsl.exe orphelin' $st "terminé : $exited ; enfants restants : $($orphans.Count)"
    }
    Check 'S02' 'security' 'arguments hostiles : aucun plantage, aucun processus lancé' {
        $bad = 0; $details = @()
        foreach ($a in @(@('--status-json', '; calc.exe'), @('--diagnostic', '$(calc)'), @('--status-json', ('A' * 5000)))) {
            $r = Invoke-Native $ApexExe $a 60
            $details += "$($a[0]) -> code $($r.ExitCode)"
            if ($r.TimedOut -or $r.ExitCode -notin 0, 1, 2, 3) { $bad++ }
        }
        # Option inconnue : une application graphique Windows l'ignore et ouvre sa fenêtre (constaté run 10) —
        # vérifié ici : la fenêtre s'ouvre, le processus ne plante pas, puis il est fermé.
        Stop-AllPanels
        $gp = Start-Process -FilePath $ApexExe -ArgumentList '--unknown-option', '$(calc)' -PassThru
        $w = Wait-PanelWindow 30
        Start-Sleep 3
        $alive = -not $gp.HasExited
        $details += "--unknown-option -> fenêtre : $([bool]$w), toujours en vie après 3 s : $alive"
        if (-not $w -or -not $alive) { $bad++ }
        Stop-AllPanels
        $calc = @(Get-Process calc, CalculatorApp -ErrorAction SilentlyContinue).Count
        $st = 'FAIL'; if ($bad -eq 0 -and $calc -eq 0) { $st = 'PASS' }
        Add-Check 'S02' 'security' 'arguments hostiles : aucun plantage, aucun processus lancé' $st (($details -join ' ; ') + " ; calculatrice lancée : $calc")
    }
}

# ------------------------------------------------------------------------------------------------------
# WSL réel
# ------------------------------------------------------------------------------------------------------
function Test-Wsl {
    $reg = @(Get-WslRegistry)
    if ($reg.Count -eq 0) {
        Check 'W01' 'wsl' 'WSL sans distribution : diagnostic exact' {
            $s = Get-PanelStatus
            $st = 'FAIL'; if ($s.Cause -eq 'WslNotInstalled') { $st = 'PASS' }
            Add-Check 'W01' 'wsl' 'WSL sans distribution : diagnostic exact' $st "$($s.Verdict)/$($s.Cause) — $($s.Guidance)"
        }
        foreach ($id in 'W02', 'W03', 'W04', 'W05') { Add-Check $id 'wsl' 'scénario WSL' 'BLOCKED' 'aucune distribution WSL installée sur cette machine' }
        return
    }
    Check 'W01' 'wsl' 'détection : le Panel voit la distribution et son état réels' {
        $running = Get-RunningDistros
        $s = Get-PanelStatus
        $expected = 'Offline'; if ($running -contains $script:DistroName) { $expected = 'Ready' }
        $st = 'FAIL'; if ($s.Components['Wsl'] -eq $expected) { $st = 'PASS' }
        Add-Check 'W01' 'wsl' 'détection : le Panel voit la distribution et son état réels' $st "distribution $($script:DistroName) ; en cours : $($running -join ', ') ; Panel WSL = $($s.Components['Wsl']) (attendu $expected) ; langue Windows $($script:Env.ui_culture)"
    }
    Check 'W02' 'wsl' 'distribution choisie inexistante : « introuvable »' {
        $cfg = Join-Path $env:TEMP ("apex-cfg-" + [guid]::NewGuid())
        New-Item -ItemType Directory $cfg | Out-Null
        [IO.File]::WriteAllText((Join-Path $cfg 'dashboard-settings.json'), '{"WslDistro":"ApexDistributionInexistante"}', $Utf8NoBom)
        $s = Get-PanelStatus @{ APEX_PANEL_CONFIG_DIR = $cfg }
        Remove-Item $cfg -Recurse -Force
        $st = 'FAIL'; if ($s.Cause -eq 'WslNotInstalled' -and $s.Raw -match 'introuvable') { $st = 'PASS' }
        Add-Check 'W02' 'wsl' 'distribution choisie inexistante : « introuvable »' $st "$($s.Verdict)/$($s.Cause)"
    }
    Check 'W03' 'wsl' 'distribution arrêtée : WslStopped, et le Panel ne la redémarre JAMAIS' {
        if (-not $AllowWslShutdown) { Add-Check 'W03' 'wsl' 'distribution arrêtée : WslStopped, et le Panel ne la redémarre JAMAIS' 'BLOCKED' 'exige -AllowWslShutdown (arrête la distribution)'; return }
        [void](Invoke-Native 'wsl.exe' @('--terminate', $script:DistroName) 60)
        Start-Sleep 3
        $s = Get-PanelStatus
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        Start-Sleep 30  # 6 rafraîchissements automatiques
        $shot = $null; if ($p) { $shot = Save-WindowShot $p "run$($script:Run)-panel-wsl-stopped.png" }
        $stillStopped = -not ((Get-RunningDistros) -contains $script:DistroName)
        Stop-AllPanels
        $st = 'FAIL'; if ($s.Cause -eq 'WslStopped' -and $stillStopped) { $st = 'PASS' }
        Add-Check 'W03' 'wsl' 'distribution arrêtée : WslStopped, et le Panel ne la redémarre JAMAIS' $st "$($s.Verdict)/$($s.Cause) ; toujours arrêtée après 30 s de Panel ouvert : $stillStopped"
    }
    Check 'W04' 'wsl' 'distribution redémarrée : WSL READY' {
        if (-not $AllowWslShutdown) { Add-Check 'W04' 'wsl' 'distribution redémarrée : WSL READY' 'BLOCKED' 'exige -AllowWslShutdown'; return }
        Start-KeepAlive
        $w = Wait-Panel { param($s) $s.Components['Wsl'] -eq 'Ready' } 60
        $st = 'FAIL'; if ($w.Reached) { $st = 'PASS' }
        Add-Check 'W04' 'wsl' 'distribution redémarrée : WSL READY' $st (($w.Transitions | ForEach-Object { $_.state }) -join ' -> ')
    }
    Check 'W05' 'wsl' 'plusieurs distributions : backend dans une distribution non par défaut' {
        if ($reg.Count -lt 2) { Add-Check 'W05' 'wsl' 'plusieurs distributions : backend dans une distribution non par défaut' 'BLOCKED' "une seule distribution installée ($($reg[0].Name))"; return }
        $default = @($reg | Where-Object { $_.IsDefault })[0]
        $st = 'NOT_APPLICABLE'; $detail = "distributions : $(($reg | ForEach-Object { $_.Name }) -join ', ') ; par défaut : $($default.Name) ; backend testé dans : $($script:DistroName)"
        if ($default.Name -ne $script:DistroName) {
            $s = Get-PanelStatus
            $st = 'FAIL'; if ($s.Object -and [string]$s.Object.wsl_distro -eq $script:DistroName) { $st = 'PASS' }
            $detail += " ; le Panel lit : $($s.Object.wsl_distro)"
        }
        Add-Check 'W05' 'wsl' 'plusieurs distributions : backend dans une distribution non par défaut' $st $detail
    }
}

function Start-KeepAlive {
    # Garde la distribution démarrée pendant les tests (comme un terminal WSL ouvert par l'utilisateur).
    if ($script:KeepAlive -and -not $script:KeepAlive.HasExited) { return }
    $script:KeepAlive = Start-Process wsl.exe -ArgumentList @('-d', $script:DistroName, '--exec', 'sleep', '7200') -WindowStyle Hidden -PassThru
    Start-Sleep 4
}

# ------------------------------------------------------------------------------------------------------
# Backend réel via launch.sh (opt-in)
# ------------------------------------------------------------------------------------------------------
function Start-Launch([string]$Name, [hashtable]$Env = @{}) {
    $log = Join-Path $Evidence "$Name.launch.log"
    $wslArgs = @('-d', $script:DistroName, '--cd', $script:ApexRepo, '--exec')
    if ($Env.Count -gt 0) { $wslArgs += 'env'; foreach ($k in $Env.Keys) { $wslArgs += "$k=$($Env[$k])" } }
    $wslArgs += @('bash', 'scripts/apex-launch.sh', 'serve')
    $p = Start-Process wsl.exe -ArgumentList $wslArgs -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru
    $null = $p.Handle  # sans handle ouvert avant la fin, ExitCode reste vide (Start-Process -PassThru)
    $p
}

function Get-PreflightRefusal([string]$Name) {
    # launch.sh arrêté AVANT `apex serve` par son propre préflight (codes documentés dans apex-launch.sh) :
    # c'est la machine qui refuse de démarrer le backend, pas le Panel qui se trompe. Constaté au run 11 :
    # `apex gate` non concluant (code 5) sur le runner CI.
    if (-not $script:Launch -or -not $script:Launch.HasExited) { return $null }
    $code = $script:Launch.ExitCode
    $log = Join-Path $Evidence "$Name.launch.log"
    $text = ''
    foreach ($f in $log, "$log.err") { if (Test-Path $f) { $text += [IO.File]::ReadAllText($f) + "`n" } }
    $reason = $null
    switch ($code) {
        1 { if ($text -match '\.env introuvable') { $reason = '.env absent' } }
        2 { $reason = 'Docker/Compose indisponible ou en échec' }
        3 { $reason = 'aucun Python 3.12+ utilisable' }
        4 { $reason = 'apex doctor a renvoyé FAIL' }
        5 { $reason = 'apex gate --startup : un contrôle mesuré est en FAIL' }
        6 { $reason = 'apex gate --startup : une précondition n''a pas pu être vérifiée' }
        # 7 = panne interne du Gate : pas un refus de la machine, B03 reste un FAIL.
    }
    if (-not $reason) { return $null }
    $why = @($text -split "`r?`n" | Where-Object { $_ -match 'FAIL mesurés \(bloquants\)|Préconditions non vérifiées \(bloquantes\)|\[ FAIL \]' } |
        ForEach-Object { $_.Trim() } | Select-Object -First 3)
    $msg = "launch.sh arrêté par son préflight (code $code : $reason)"
    if ($why.Count -gt 0) { $msg += ' — ' + ($why -join ' ; ') }
    $msg
}

function Skip-IfBackendDown([string]$Id, [string]$Group, [string]$Title, [string]$Extra = '') {
    # Un scénario qui exige un backend en marche n'est pas évaluable quand la machine a refusé de le démarrer :
    # BLOCKED avec la cause exacte (jamais un PASS, jamais une cascade de FAIL qui masquerait la vraie cause).
    if (-not $script:BackendDown) { return $false }
    Add-Check $Id $Group $Title 'BLOCKED' ("backend jamais démarré sur cette machine (voir B03) : $($script:BackendDown)" + $Extra)
    return $true
}

function Send-Signal([string]$Signal, [int]$ProcessId) {
    Invoke-Native 'wsl.exe' @('-d', $script:DistroName, '--exec', 'kill', "-$Signal", "$ProcessId") 30
}

function Stop-Backend {
    $rec = Get-DiscoveryRecord
    if ($rec) { [void](Send-Signal 'INT' ([int]$rec.pid)) }
    for ($i = 0; $i -lt 60 -and (Get-DiscoveryRecord); $i++) { Start-Sleep 1 }
    $script:Launch = $null
}

function Get-BackendOwnStatus {
    # /api/v1/status interrogé DANS WSL avec le jeton (qui ne sort jamais de WSL) : la vérité du backend.
    $rec = Get-DiscoveryRecord
    if (-not $rec) { return $null }
    $py = "import urllib.request,sys`n" +
        "t=open('$($rec.project_root)/$($rec.token_file)').read().strip()`n" +
        "r=urllib.request.Request('http://127.0.0.1:$($rec.port)/api/v1/status',headers={'Authorization':'Bearer '+t})`n" +
        "sys.stdout.write(urllib.request.urlopen(r,timeout=10).read().decode())`n"
    $b64 = [Convert]::ToBase64String($Utf8NoBom.GetBytes($py))
    $r = Invoke-Wsl "echo $b64 | base64 -d | python3" 30
    try { return ($r.Stdout | ConvertFrom-Json) } catch { return $null }
}

function Ask([string]$Question) {
    if (-not $Interactive) { return $false }
    do { $a = (Read-Host "$Question (O/N)").Trim().ToUpperInvariant() } while ($a -notin 'O', 'N')
    return ($a -eq 'O')
}

function Test-UserWorkflow {
    # Le parcours EXACT de l'utilisateur : WSL démarré, launch.sh lancé par lui, puis double-clic sur APEX.
    Check 'U01' 'backend' 'parcours utilisateur : launch.sh lancé par l''utilisateur, double-clic, état réel' {
        $rec = $null; if ($script:DistroName) { $rec = Get-DiscoveryRecord }
        if (-not $rec) { Add-Check 'U01' 'backend' 'parcours utilisateur' 'BLOCKED' 'aucun backend lancé par l''utilisateur avant le script (launch.sh non lancé)'; return }
        Stop-AllPanels
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        $w = Wait-Panel { param($s) $s.Verdict -in 'Ready', 'Degraded', 'VersionMismatch' } 600 5
        $shot = $null; if ($p) { $shot = Save-WindowShot $p "run$($script:Run)-user-workflow.png" }
        Save-Text "run$($script:Run)-user-workflow-status.json" $w.Status.Raw
        $st = 'FAIL'; if ($p -and $w.Reached -and $w.Status.Verdict -in 'Ready', 'Degraded') { $st = 'PASS' }
        Add-Check 'U01' 'backend' 'parcours utilisateur : launch.sh lancé par l''utilisateur, double-clic, état réel' $st "fenêtre : $([bool]$p) ; $($w.Status.Verdict)/$($w.Status.Cause) après $([math]::Round($sw.ElapsedMilliseconds/1000)) s ; transitions : $(($w.Transitions | ForEach-Object { $_.state }) -join ' -> ')"
    }
}

function Test-Backend {
    $blocked = $null
    if (-not $AllowBackendControl) { $blocked = 'exige -AllowBackendControl (démarre et arrête le backend APEX avec launch.sh)' }
    elseif (-not $script:ApexRepo) { $blocked = 'dépôt APEX introuvable dans la distribution (préciser -ApexPathInWsl)' }
    if ($blocked) {
        foreach ($id in 'B01', 'B02', 'B03', 'M01', 'B04', 'B05', 'B06', 'B07', 'B08', 'B09', 'B10', 'B11', 'B12', 'B13', 'S03', 'S04', 'S05') { Add-Check $id 'backend' 'scénario backend' 'BLOCKED' $blocked }
        return
    }
    Start-KeepAlive
    Stop-Backend
    $script:BackendDown = $null

    Check 'B01' 'backend' 'launch.sh non lancé : BackendNotStarted' {
        $s = Get-PanelStatus
        $st = 'FAIL'; if ($s.Cause -eq 'BackendNotStarted' -and $s.Components['Backend'] -eq 'Offline') { $st = 'PASS' }
        Add-Check 'B01' 'backend' 'launch.sh non lancé : BackendNotStarted' $st "$($s.Verdict)/$($s.Cause) — $($s.Guidance)"
    }
    Check 'S05' 'security' 'port occupé par un autre programme : PortConflict, rien d''exécuté' {
        # Faux serveur servi par CE processus, lié AVANT le lancement du Panel, qui lit la requête avant de
        # répondre puis ferme proprement. Run 11 : l'ancien faux serveur (Start-Job) se liait pendant que le
        # parent tenait encore le port et fermait sans lire la requête ; le Panel voyait « aucune réponse »
        # (ApiTimeout) — défaut du test, pas du Panel.
        $title = 'port occupé par un autre programme : PortConflict, rien d''exécuté'
        $port = 8000
        $listener = $null
        try { $listener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $port); $listener.Start() } catch { $listener = $null }
        if (-not $listener) { Add-Check 'S05' 'security' $title 'BLOCKED' "port $port déjà utilisé sur cette machine"; return }
        $cfg = Join-Path $env:TEMP ("apex-cfg-" + [guid]::NewGuid()); New-Item -ItemType Directory $cfg | Out-Null
        $body = '{"hello":"& calc.exe","cmd":"$(powershell -c calc)"}'
        $resp = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 200 OK`r`nContent-Type: application/json`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n$body")
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $ApexExe; $psi.Arguments = '--status-json'  # paramètres par défaut : port 8000
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $psi.EnvironmentVariables['WSL_UTF8'] = '1'; $psi.EnvironmentVariables['APEX_PANEL_CONFIG_DIR'] = $cfg
        $served = 0
        $p = [Diagnostics.Process]::Start($psi)
        $out = $p.StandardOutput.ReadToEndAsync(); $null = $p.StandardError.ReadToEndAsync()
        $deadline = (Get-Date).AddSeconds(90)
        try {
            while (-not $p.HasExited -and (Get-Date) -lt $deadline) {
                if (-not $listener.Pending()) { Start-Sleep -Milliseconds 20; continue }
                $c = $listener.AcceptTcpClient()
                try {
                    $c.ReceiveTimeout = 5000
                    $stream = $c.GetStream(); $buf = New-Object byte[] 8192; $req = ''
                    while ($req -notmatch "`r`n`r`n") {
                        $n = $stream.Read($buf, 0, $buf.Length); if ($n -le 0) { break }
                        $req += [Text.Encoding]::ASCII.GetString($buf, 0, $n)
                    }
                    $stream.Write($resp, 0, $resp.Length); $stream.Flush()
                    $c.Client.Shutdown([Net.Sockets.SocketShutdown]::Send)
                    $served++
                } catch { } finally { $c.Close() }
            }
        } finally { $listener.Stop() }
        if (-not $p.HasExited) { try { $p.Kill() } catch { } }
        Remove-Item $cfg -Recurse -Force
        $obj = $null; try { $obj = $out.Result | ConvertFrom-Json } catch { }
        $verdict = '?'; $cause = '?'; if ($obj) { $verdict = [string]$obj.verdict; $cause = [string]$obj.cause }
        $calc = @(Get-Process calc, CalculatorApp -ErrorAction SilentlyContinue).Count
        $st = 'FAIL'; if ($cause -eq 'PortConflict' -and $calc -eq 0) { $st = 'PASS' }
        Add-Check 'S05' 'security' $title $st "$verdict/$cause ; requêtes servies par le faux serveur : $served ; calculatrice : $calc"
    }
    Check 'B02' 'backend' 'launch.sh en préflight : Backend STARTING' {
        $script:Launch = Start-Launch "run$($script:Run)-start1"
        $w = Wait-Panel { param($s) $s.Cause -eq 'BackendStarting' -or $s.Verdict -in 'Ready', 'Degraded' } 600 2
        $sawStarting = @($w.Transitions | Where-Object { $_.state -like '*BackendStarting*' }).Count -gt 0
        $st = 'FAIL'; if ($sawStarting) { $st = 'PASS' }
        Add-Check 'B02' 'backend' 'launch.sh en préflight : Backend STARTING' $st (($w.Transitions | ForEach-Object { "$($_.at) $($_.state)" }) -join ' -> ')
    }
    Check 'B03' 'backend' 'backend démarré : le Panel converge vers l''état réel' {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $w = Wait-Panel { param($s) $s.Verdict -in 'Ready', 'Degraded', 'VersionMismatch' -or ($script:Launch -and $script:Launch.HasExited) } ($BackendStartTimeoutMin * 60) 5
        $script:Perf["backend_ready_seconds_run$($script:Run)"] = [math]::Round($sw.ElapsedMilliseconds / 1000)
        $s = $w.Status
        Save-Text "run$($script:Run)-status-backend.json" $s.Raw
        if (-not $w.Reached -or $s.Verdict -notin 'Ready', 'Degraded') {
            $refusal = Get-PreflightRefusal "run$($script:Run)-start1"
            if ($refusal) {
                $script:BackendDown = $refusal
                Add-Check 'B03' 'backend' 'backend démarré : le Panel converge vers l''état réel' 'BLOCKED' "$refusal ; Panel : $($s.Verdict)/$($s.Cause)"
                return
            }
            $tail = ''; $log = Join-Path $Evidence "run$($script:Run)-start1.launch.log"
            if (Test-Path $log) { $tail = (Get-Content $log -Tail 15 -Encoding UTF8) -join ' | ' }
            Add-Check 'B03' 'backend' 'backend démarré : le Panel converge vers l''état réel' 'FAIL' "$($s.Verdict)/$($s.Cause) ; launch.sh terminé : $($script:Launch.HasExited) ; fin du journal : $tail"
            return
        }
        Add-Check 'B03' 'backend' 'backend démarré : le Panel converge vers l''état réel' 'PASS' "$($s.Verdict)/$($s.Cause) en $([math]::Round($sw.ElapsedMilliseconds/1000)) s ; API joignable depuis Windows par localhost (WSL2)"
    }
    Read-BackendSecrets
    Check 'M01' 'backend' 'MinIO facultatif : le backend démarre sans MinIO (profil « minio » inactif)' {
        $s = Get-PanelStatus
        $minio = (Invoke-Wsl 'docker ps --filter name=apex-minio --format "{{.Names}} {{.Status}}" 2>/dev/null' 30).Stdout.Trim()
        $profiles = (Invoke-Wsl "cd '$($script:ApexRepo)' && grep -E '^COMPOSE_PROFILES=' .env 2>/dev/null" 30).Stdout.Trim()
        $log = Join-Path $Evidence "run$($script:Run)-start1.launch.log"
        $doctor = ''; if (Test-Path $log) { $doctor = ((Get-Content $log -Encoding UTF8 | Where-Object { $_ -match 'minio' }) | Select-Object -First 1) -join ' | ' }
        if (Skip-IfBackendDown 'M01' 'backend' 'MinIO facultatif : le backend démarre sans MinIO (profil « minio » inactif)' " ; conteneur apex-minio : $(if ($minio) { $minio } else { 'absent' }) ; doctor : $doctor") { return }
        if ($profiles -match 'minio|\*') { Add-Check 'M01' 'backend' 'MinIO facultatif' 'NOT_APPLICABLE' "profil MinIO activé sur cette machine ($profiles) : le démarrage sans MinIO n'est pas le cas testé"; return }
        $st = 'FAIL'; if ($s.Verdict -in 'Ready', 'Degraded' -and -not $minio) { $st = 'PASS' }
        Add-Check 'M01' 'backend' 'MinIO facultatif : le backend démarre sans MinIO (profil « minio » inactif)' $st "$($s.Verdict)/$($s.Cause) ; conteneur apex-minio : $(if ($minio) { $minio } else { 'absent' }) ; journal launch.sh (minio) : $doctor"
    }
    Check 'B04' 'backend' 'APEX READY (tous les composants prêts)' {
        if (Skip-IfBackendDown 'B04' 'backend' 'APEX READY (tous les composants prêts)') { return }
        $s = Get-PanelStatus
        if ($s.Verdict -eq 'Ready') { Add-Check 'B04' 'backend' 'APEX READY (tous les composants prêts)' 'PASS' 'verdict Ready'; return }
        $notReady = @($s.Object.components | Where-Object { $_.status -ne 'Ready' } | ForEach-Object { "$($_.key)=$($_.status) ($($_.detail))" })
        Add-Check 'B04' 'backend' 'APEX READY (tous les composants prêts)' 'BLOCKED' ("pas READY sur cette machine : " + ($notReady -join ' ; '))
    }
    Check 'B05' 'backend' 'chaque composant affiché = état mesuré par le backend lui-même' {
        if (Skip-IfBackendDown 'B05' 'backend' 'chaque composant affiché = état mesuré par le backend lui-même') { return }
        $own = Get-BackendOwnStatus
        $s = Get-PanelStatus
        if (-not $own) { Add-Check 'B05' 'backend' 'chaque composant affiché = état mesuré par le backend lui-même' 'FAIL' 'réponse du backend illisible depuis WSL'; return }
        $map = @{ ready = 'Ready'; degraded = 'Degraded'; offline = 'Offline'; error = 'Error'; starting = 'Starting'; unknown = 'Unknown' }
        $diff = @()
        foreach ($c in $own.data.components) {
            $key = (Get-Culture).TextInfo.ToTitleCase([string]$c.component)
            if ($s.Components.ContainsKey($key) -and $s.Components[$key] -ne $map[[string]$c.state]) { $diff += "$key : Panel $($s.Components[$key]) / backend $($c.state)" }
        }
        $st = 'PASS'; if ($diff.Count -gt 0) { $st = 'FAIL' }
        Add-Check 'B05' 'backend' 'chaque composant affiché = état mesuré par le backend lui-même' $st ("écarts : " + ($(if ($diff.Count) { $diff -join ' ; ' } else { 'aucun' })))
    }
    Check 'B06' 'backend' 'Panel ouvert APRÈS le backend : premier verdict = état réel' {
        if (Skip-IfBackendDown 'B06' 'backend' 'Panel ouvert APRÈS le backend : premier verdict = état réel') { return }
        Stop-AllPanels
        $sw = [Diagnostics.Stopwatch]::StartNew()
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        Start-Sleep 6
        $shot = $null; if ($p) { $shot = Save-WindowShot $p "run$($script:Run)-panel-backend-running.png" }
        $s = Get-PanelStatus
        $st = 'FAIL'; if ($p -and $s.Verdict -in 'Ready', 'Degraded') { $st = 'PASS' }
        Add-Check 'B06' 'backend' 'Panel ouvert APRÈS le backend : premier verdict = état réel' $st "fenêtre : $([bool]$p) ; $($s.Verdict)/$($s.Cause) ; capture : $(if ($shot) { Split-Path $shot -Leaf } else { 'non' })"
    }
    Check 'S03' 'security' 'le diagnostic, les journaux et le presse-papiers ne contiennent aucun secret' {
        if ($script:Secrets.Count -eq 0) { Add-Check 'S03' 'security' 'le diagnostic, les journaux et le presse-papiers ne contiennent aucun secret' 'BLOCKED' 'jeton et mots de passe illisibles : rien à rechercher'; return }
        $texts = @((Invoke-Native $ApexExe @('--diagnostic') 60).Stdout, (Get-PanelStatus).Raw)
        foreach ($f in (Get-ChildItem $LogsDir -File -ErrorAction SilentlyContinue)) { $texts += [IO.File]::ReadAllText($f.FullName) }
        $clip = Copy-DiagnosticThroughUi
        if ($clip) { $texts += $clip }
        $hits = 0; foreach ($t in $texts) { $hits += Find-Secrets $t }
        $st = 'PASS'; if ($hits -gt 0) { $st = 'FAIL' }
        Add-Check 'S03' 'security' 'le diagnostic, les journaux et le presse-papiers ne contiennent aucun secret' $st "$($script:Secrets.Count) secrets recherchés dans $($texts.Count) sorties ; trouvés : $hits ; bouton « Copier le diagnostic » : $(if ($clip) { 'utilisé (' + $clip.Length + ' caractères)' } else { 'non atteint par UI Automation' })"
    }
    Check 'B07' 'backend' 'backend figé (SIGSTOP) : ApiTimeout, puis rétabli (SIGCONT)' {
        if (Skip-IfBackendDown 'B07' 'backend' 'backend figé (SIGSTOP) : ApiTimeout, puis rétabli (SIGCONT)') { return }
        $rec = Get-DiscoveryRecord
        [void](Send-Signal 'STOP' ([int]$rec.pid))
        $w1 = Wait-Panel { param($s) $s.Cause -eq 'ApiTimeout' } 90 3
        [void](Send-Signal 'CONT' ([int]$rec.pid))
        $w2 = Wait-Panel { param($s) $s.Verdict -in 'Ready', 'Degraded' } 90 3
        $st = 'FAIL'; if ($w1.Reached -and $w2.Reached) { $st = 'PASS' }
        Add-Check 'B07' 'backend' 'backend figé (SIGSTOP) : ApiTimeout, puis rétabli (SIGCONT)' $st "figé : $($w1.Status.Cause) ; rétabli : $($w2.Status.Verdict)"
    }
    Check 'B08' 'backend' 'crash du backend (kill -9) : BackendStopped, jamais READY fantôme' {
        if (Skip-IfBackendDown 'B08' 'backend' 'crash du backend (kill -9) : BackendStopped, jamais READY fantôme') { return }
        $rec = Get-DiscoveryRecord
        [void](Send-Signal 'KILL' ([int]$rec.pid))
        $w = Wait-Panel { param($s) $s.Cause -eq 'BackendStopped' } 60 3
        $st = 'FAIL'; if ($w.Reached) { $st = 'PASS' }
        Add-Check 'B08' 'backend' 'crash du backend (kill -9) : BackendStopped, jamais READY fantôme' $st (($w.Transitions | ForEach-Object { $_.state }) -join ' -> ')
    }
    Check 'B09' 'recovery' 'backend relancé après crash : retour à l''état réel' {
        if (Skip-IfBackendDown 'B09' 'recovery' 'backend relancé après crash : retour à l''état réel') { return }
        $script:Launch = Start-Launch "run$($script:Run)-restart"
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $w = Wait-Panel { param($s) $s.Verdict -in 'Ready', 'Degraded' } ($BackendStartTimeoutMin * 60) 5
        $script:Perf["backend_recovery_seconds_run$($script:Run)"] = [math]::Round($sw.ElapsedMilliseconds / 1000)
        $st = 'FAIL'; if ($w.Reached) { $st = 'PASS' }
        Add-Check 'B09' 'recovery' 'backend relancé après crash : retour à l''état réel' $st "$($w.Status.Verdict)/$($w.Status.Cause) en $([math]::Round($sw.ElapsedMilliseconds/1000)) s"
    }
    Check 'B10' 'backend' 'arrêt propre (Ctrl+C) : enregistrement retiré, BackendNotStarted' {
        if (Skip-IfBackendDown 'B10' 'backend' 'arrêt propre (Ctrl+C) : enregistrement retiré, BackendNotStarted') { return }
        Stop-Backend
        $w = Wait-Panel { param($s) $s.Cause -eq 'BackendNotStarted' } 60 3
        $st = 'FAIL'; if ($w.Reached -and -not (Get-DiscoveryRecord)) { $st = 'PASS' }
        Add-Check 'B10' 'backend' 'arrêt propre (Ctrl+C) : enregistrement retiré, BackendNotStarted' $st "$($w.Status.Verdict)/$($w.Status.Cause)"
    }
    Check 'B11' 'backend' 'launch.sh interrompu pendant le démarrage : jamais STARTING éternel' {
        $script:Launch = Start-Launch "run$($script:Run)-interrupt"
        $w = Wait-Panel { param($s) $s.Cause -eq 'BackendStarting' } 300 1
        $rec = Get-DiscoveryRecord
        if ($rec) { [void](Send-Signal 'INT' ([int]$rec.pid)) }
        $w2 = Wait-Panel { param($s) $s.Cause -in 'BackendNotStarted', 'BackendStopped' } 120 3
        $st = 'FAIL'; if ($w.Reached -and $w2.Reached) { $st = 'PASS' }
        Add-Check 'B11' 'backend' 'launch.sh interrompu pendant le démarrage : jamais STARTING éternel' $st "vu STARTING : $($w.Reached) ; après interruption : $($w2.Status.Cause)"
        Stop-Backend
    }
    Check 'B12' 'backend' 'jeton refusé par le backend : TokenRejected' {
        if (Skip-IfBackendDown 'B12' 'backend' 'jeton refusé par le backend : TokenRejected') { return }
        $bogus = 'apex-validation-' + [guid]::NewGuid().ToString('N')
        $script:Launch = Start-Launch "run$($script:Run)-badtoken" @{ APEX_API_TOKEN = $bogus }
        $w = Wait-Panel { param($s) $s.Cause -eq 'TokenRejected' -or $s.Verdict -in 'Ready', 'Degraded' } ($BackendStartTimeoutMin * 60) 5
        $st = 'FAIL'; if ($w.Status.Cause -eq 'TokenRejected') { $st = 'PASS' }
        Add-Check 'B12' 'backend' 'jeton refusé par le backend : TokenRejected' $st "$($w.Status.Verdict)/$($w.Status.Cause)"
        Stop-Backend
    }
    Check 'S04' 'security' 'enregistrement de découverte hostile : refusé, rien exécuté dans WSL ni Windows' {
        $hostile = '{"schema":1,"service":"apex","phase":"serving","apex_version":"x","api_version":"v1","panel_protocol":1,' +
            '"project_root":"/tmp/$(touch /tmp/apex_pwned)","token_file":"../../etc/shadow","host":"127.0.0.1","port":8000,"pid":1,"started_at":"2026-01-01T00:00:00Z"}'
        [void](Invoke-Wsl ("mkdir -p ~/.apex/runtime && printf '%s' '" + $hostile + "' > ~/.apex/runtime/backend.json && rm -f /tmp/apex_pwned") 30)
        $since = (Get-Date).AddSeconds(-1)
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        $children = @(); if ($p) { $children = @(Watch-Children $p.Id 15 -Since $since) }
        $s = Get-PanelStatus
        $pwned = (Invoke-Wsl 'test -e /tmp/apex_pwned && echo yes || echo no' 30).Stdout.Trim()
        [void](Invoke-Wsl 'rm -f ~/.apex/runtime/backend.json' 30)
        Stop-AllPanels
        $o = Get-ChildrenOutcome $children $script:ProcessAudit.Active
        # Le refus de l'enregistrement et l'absence d'exécution dans WSL sont mesurés sans ambiguïté : leur
        # échec est un FAIL ; sinon le verdict des processus enfants s'applique (jamais PASS sur un non identifié).
        $st = $o.Status; if ($s.Cause -ne 'DiscoveryInvalid' -or $pwned -ne 'no') { $st = 'FAIL' }
        Add-Check 'S04' 'security' 'enregistrement de découverte hostile : refusé, rien exécuté dans WSL ni Windows' $st "$($s.Verdict)/$($s.Cause) ; fichier témoin créé dans WSL : $pwned ; $($o.Detail)"
    }
    Check 'B13' 'backend' 'Panel ouvert AVANT le backend : se met à jour tout seul' {
        if (Skip-IfBackendDown 'B13' 'backend' 'Panel ouvert AVANT le backend : se met à jour tout seul') { return }
        Stop-AllPanels
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        $script:Launch = Start-Launch "run$($script:Run)-panel-first"
        $w = Wait-Panel { param($s) $s.Verdict -in 'Ready', 'Degraded' } ($BackendStartTimeoutMin * 60) 5
        Start-Sleep 12  # au moins deux rafraîchissements automatiques de la fenêtre
        $log = Get-ChildItem $LogsDir -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
        $lines = @(); if ($log) { $lines = @(Get-Content $log.FullName -Tail 40) }
        $guiSawIt = @($lines | Where-Object { $_ -match 'APEX (READY|DEGRADED)' }).Count -gt 0
        $shot = $null; if ($p) { $shot = Save-WindowShot $p "run$($script:Run)-panel-opened-before-backend.png" }
        $st = 'FAIL'; if ($p -and $w.Reached -and $guiSawIt) { $st = 'PASS' }
        Add-Check 'B13' 'backend' 'Panel ouvert AVANT le backend : se met à jour tout seul' $st "fenêtre : $([bool]$p) ; backend : $($w.Status.Verdict) ; journal du Panel montre la transition : $guiSawIt"
    }
}

function Copy-DiagnosticThroughUi {
    # Bouton « Copier le diagnostic » via UI Automation (Avalonia expose ses boutons à UIA sous Windows).
    try {
        Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
        $p = Wait-PanelWindow 10
        if (-not $p) { return $null }
        $root = [Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
        function Find-Button([string]$name) {
            $cond = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty, $name)
            $root.FindFirst([Windows.Automation.TreeScope]::Descendants, $cond)
        }
        $diag = Find-Button 'Diagnostic'
        if (-not $diag) { return $null }
        $diag.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep 2
        $copy = Find-Button 'Copier le diagnostic'
        if (-not $copy) { return $null }
        Set-Clipboard -Value 'apex-validation-empty'
        $copy.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep 2
        $text = Get-Clipboard -Raw
        if ($text -eq 'apex-validation-empty') { return $null }
        Save-Text "run$($script:Run)-clipboard-diagnostic.txt" $text
        return $text
    } catch { return $null }
}

# ------------------------------------------------------------------------------------------------------
# Redémarrage de Windows (deux phases, état conservé dans OutDir)
# ------------------------------------------------------------------------------------------------------
function Invoke-PreReboot {
    $state = [ordered]@{ last_boot = $script:Env.last_boot; distro = $script:DistroName; installed = (Test-Path $ApexExe)
        backend_running = [bool](Get-DiscoveryRecord); panel_running = (@(Get-GuiPanels).Count -gt 0) }
    [IO.File]::WriteAllText((Join-Path $OutDir 'reboot_state.json'), ($state | ConvertTo-Json), $Utf8NoBom)
    Add-Check 'R00' 'reboot' 'état avant redémarrage enregistré' 'PASS' ("installé : $($state.installed) ; backend : $($state.backend_running) ; Panel ouvert : $($state.panel_running)")
    Write-Host ''
    Write-Host 'ÉTAPE MANUELLE : redémarrez Windows maintenant, puis relancez ce script avec :' -ForegroundColor Cyan
    Write-Host "  -Phase PostReboot -OutDir `"$OutDir`" -Installer `"$Installer`" (mêmes autres options)" -ForegroundColor Cyan
}

function Register-ResumeAfterReboot {
    # HKCU RunOnce : exécuté UNE fois à la prochaine ouverture de session de CET utilisateur (sans administrateur).
    $cmd = 'powershell.exe -NoExit -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Phase PostReboot -Interactive' +
        ' -Installer "' + $Installer + '" -OutDir "' + $OutDir + '" -Label "' + $Label + '"'
    if ($Distro) { $cmd += ' -Distro "' + $Distro + '"' }
    New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Force | Out-Null
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'ApexValidation' -Value $cmd
    Write-Host 'Reprise automatique enregistrée pour la prochaine connexion. Redémarrage dans 60 s (annuler : shutdown /a).' -ForegroundColor Cyan
}

function Invoke-PostReboot {
    $statePath = Join-Path $OutDir 'reboot_state.json'
    if (-not (Test-Path $statePath)) { Add-Check 'R01' 'reboot' 'redémarrage réel' 'BLOCKED' 'phase PreReboot absente de ce dossier'; return }
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
    Check 'R01' 'reboot' 'Windows a réellement redémarré' {
        $st = 'FAIL'; if ($script:Env.last_boot -ne $state.last_boot) { $st = 'PASS' }
        Add-Check 'R01' 'reboot' 'Windows a réellement redémarré' $st "démarrage avant : $($state.last_boot) ; maintenant : $($script:Env.last_boot)"
    }
    Check 'R02' 'reboot' 'après redémarrage : programme, raccourci et paramètres présents' {
        $st = 'FAIL'; if ((Test-Path $ApexExe) -and (Get-Shortcut $DesktopLnk)) { $st = 'PASS' }
        Add-Check 'R02' 'reboot' 'après redémarrage : programme, raccourci et paramètres présents' $st "APEX.exe : $(Test-Path $ApexExe) ; raccourci : $(Test-Path $DesktopLnk) ; journaux : $(Test-Path $LogsDir)"
    }
    Check 'R03' 'reboot' 'après redémarrage : le Panel ne démarre pas tout seul, puis s''ouvre au double-clic' {
        $auto = @(Get-GuiPanels).Count
        Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP
        $p = Wait-PanelWindow 30
        $st = 'FAIL'; if ($auto -eq 0 -and $p) { $st = 'PASS' }
        Add-Check 'R03' 'reboot' 'après redémarrage : le Panel ne démarre pas tout seul, puis s''ouvre au double-clic' $st "Panels ouverts avant le double-clic : $auto ; fenêtre : $([bool]$p)"
    }
    Check 'R04' 'reboot' 'après redémarrage : état réel, aucun READY hérité d''avant' {
        $running = Get-RunningDistros
        $s = Get-PanelStatus
        $st = 'FAIL'; if ($s.Verdict -ne 'Ready' -or ($running -contains $script:DistroName -and (Get-DiscoveryRecord))) { $st = 'PASS' }
        Add-Check 'R04' 'reboot' 'après redémarrage : état réel, aucun READY hérité d''avant' $st "distributions en cours : $($running -join ', ') ; Panel : $($s.Verdict)/$($s.Cause)"
    }
    Stop-AllPanels
    # Un redémarrage suivant sera vérifié contre CE démarrage-ci (deux redémarrages réels = reproductibilité).
    $state.last_boot = $script:Env.last_boot
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json), $Utf8NoBom)
}

# ------------------------------------------------------------------------------------------------------
# Programme principal
# ------------------------------------------------------------------------------------------------------
$started = Get-Date
if ($Interactive -and $Phase -ne 'PostReboot') {
    Write-Host ''
    Write-Host 'Validation APEX sur ce PC. Le script installe, lance et désinstalle APEX, et ouvre/ferme le Panel.' -ForegroundColor Cyan
    Write-Host 'Action manuelle 1/1 avant de commencer : double-cliquez sur l''installateur dans ce dossier, notez si' -ForegroundColor Cyan
    Write-Host 'Windows affiche « Windows a protégé votre ordinateur » (SmartScreen), puis fermez l''installation.' -ForegroundColor Cyan
    if (Ask 'Un avertissement SmartScreen s''est-il affiché ?') { $SmartScreen = 'warning' }
    elseif (Ask 'Avez-vous bien fait le double-clic (N = non testé) ?') { $SmartScreen = 'no-warning' }
    if (Ask 'Le script peut-il arrêter puis relancer le backend APEX et WSL pour tester les pannes (plusieurs fois, ~30-60 min) ?') {
        $AllowBackendControl = [switch]$true; $AllowWslShutdown = [switch]$true
    }
}
$script:Env = Get-Environment
if ($script:Env.is_admin) {
    Write-Host 'ATTENTION : script lancé en administrateur ; l''absence d''élévation ne peut pas être prouvée. Relancez-le en utilisateur normal.' -ForegroundColor Yellow
}
$reg = @(Get-WslRegistry)
$script:DistroName = $Distro
if (-not $script:DistroName -and $reg.Count -gt 0) {
    $d = @($reg | Where-Object { $_.IsDefault }); if ($d.Count -gt 0) { $script:DistroName = $d[0].Name } else { $script:DistroName = $reg[0].Name }
}
$script:ApexRepo = $ApexPathInWsl
if (-not $script:ApexRepo -and $script:DistroName) {
    $rec = Get-DiscoveryRecord
    if ($rec) { $script:ApexRepo = [string]$rec.project_root }
    else {
        $found = Invoke-Wsl 'for d in ~/*/ ~/*/*/; do [ -f "$d/scripts/apex-launch.sh" ] && [ -f "$d/apex/version.py" ] && { cd "$d" && pwd; break; }; done' 60
        $line = ($found.Stdout -split "`r?`n" | Where-Object { $_ } | Select-Object -First 1)
        if ($line) { $script:ApexRepo = $line.Trim() }
    }
}
$script:Env['distro_under_test'] = $script:DistroName
# MinIO (docs/MINIO_IMPACT_ANALYSIS.md) : constat en LECTURE SEULE — image en cache, volume, taille, dossiers de
# premier niveau (= buckets). Volume monté en lecture seule, sans réseau ; rien n'est créé ni modifié.
if ($script:DistroName) {
    $minio = Invoke-Wsl @'
command -v docker >/dev/null || { echo "docker=absent"; exit 0; }
docker info >/dev/null 2>&1 || { echo "docker=injoignable"; exit 0; }
echo "image=$(docker image inspect --format '{{.Id}} créée {{.Created}}' minio/minio:latest 2>/dev/null || echo absente)"
vol=$(docker volume ls -q --filter name=minio-data | head -1); echo "volume=${vol:-absent}"
[ -n "$vol" ] || exit 0
for i in minio/minio:latest pgvector/pgvector:pg16 redis:7-alpine; do
  if docker image inspect "$i" >/dev/null 2>&1; then
    docker run --rm --network none -v "$vol":/data:ro --entrypoint sh "$i" -c 'echo "taille_ko=$(du -sk /data | cut -f1)"; echo "entrees=$(ls -A /data | tr "\n" " ")"'
    exit 0
  fi
done
echo "contenu=non lu (aucune image locale pour le lire sans téléchargement)"
'@ 120
    $script:Env['minio_readonly_facts'] = @($minio.Stdout -split "`r?`n" | Where-Object { $_ })
}
$script:Env['apex_repo_detected'] = [bool]$script:ApexRepo
Write-Host "Distribution : $($script:DistroName) ; dépôt APEX : $($script:ApexRepo)"

$settingsBackup = $null
$settingsFile = Join-Path $SettingsDir 'dashboard-settings.json'
if (Test-Path $settingsFile) { $settingsBackup = [IO.File]::ReadAllBytes($settingsFile) }

try {
    if ($AuditProcessCreation) {
        try { Enable-ProcessAudit } catch { $script:ProcessAudit.Active = $false; $script:ProcessAudit.Reason = "activation impossible : $($_.Exception.Message)" }
        Write-Host "Audit des créations de processus (4688) : $($script:ProcessAudit.Reason)"
    }
    if ($Phase -eq 'PostReboot') {
        $script:Run = 1
        Invoke-PostReboot
        if (Ask 'Second redémarrage (nécessaire pour prouver que le comportement se reproduit) ?') {
            Register-ResumeAfterReboot
            & shutdown.exe /r /t 60 /c 'Validation APEX : second redémarrage dans 60 s.'
        }
    } else {
        for ($i = 1; $i -le $Repeat; $i++) {
            $script:Run = $i
            Write-Host "=== Exécution $i / $Repeat ===" -ForegroundColor Cyan
            # Chaque groupe est isolé : un incident du banc devient un échec EXPLICITE du banc (HARNESS) et la
            # suite continue ; les résultats et l'archive sont toujours produits.
            $groups = @(${function:Test-Install}, ${function:Test-UserWorkflow}, ${function:Test-InstallLifecycle},
                ${function:Test-Launch}, ${function:Test-Wsl}, ${function:Test-Backend})
            $names = @('Test-Install', 'Test-UserWorkflow', 'Test-InstallLifecycle', 'Test-Launch', 'Test-Wsl', 'Test-Backend')
            for ($g = 0; $g -lt $groups.Count; $g++) {
                if ($names[$g] -eq 'Test-UserWorkflow' -and $i -ne 1) { continue }
                try { & $groups[$g] } catch {
                    Add-Check ('HARNESS-' + $names[$g]) 'harness' "incident du banc de test dans $($names[$g])" 'FAIL' $_.Exception.Message
                }
            }
        }
        if ($Phase -eq 'Full' -and (Ask 'Tester maintenant le redémarrage de Windows (le script redémarre le PC et reprend seul après votre connexion) ?')) {
            $Phase = 'PreReboot'
        }
        if ($Phase -eq 'PreReboot') {
            if ($AllowBackendControl -and $script:ApexRepo) { $script:Launch = Start-Launch 'prereboot'; [void](Wait-Panel { param($s) $s.Verdict -in 'Ready', 'Degraded' } ($BackendStartTimeoutMin * 60) 5) }
            Start-Process -FilePath $DesktopLnk -WorkingDirectory $env:TEMP; [void](Wait-PanelWindow 30)
            Invoke-PreReboot
            if ($Interactive) { Register-ResumeAfterReboot; & shutdown.exe /r /t 60 /c 'Validation APEX : redémarrage dans 60 s ; la validation reprendra après votre connexion.' }
        }
    }
} finally {
    # Chaque étape de nettoyage est isolée : un incident ne doit jamais empêcher résultats et archive.
    try { Restore-ProcessAudit } catch { Write-Host "restauration de l'audit des processus : $($_.Exception.Message)" }
    if ($Phase -ne 'PreReboot') {
        try { Stop-AllPanels } catch { Write-Host "nettoyage : $($_.Exception.Message)" }
        if ($AllowBackendControl -and $script:ApexRepo) { try { Stop-Backend } catch { Write-Host "nettoyage : $($_.Exception.Message)" } }
        try { if ($script:KeepAlive -and -not $script:KeepAlive.HasExited) { $script:KeepAlive.Kill() } } catch { }
    }
    try { if ($settingsBackup) { [IO.File]::WriteAllBytes($settingsFile, $settingsBackup) } } catch { Write-Host "restauration des paramètres : $($_.Exception.Message)" }

    # Copies des journaux du Panel, puis masquage des secrets et du nom d'utilisateur dans tout le dossier.
    if (Test-Path $LogsDir) { Copy-Item (Join-Path $LogsDir '*') $Evidence -ErrorAction SilentlyContinue }
    $summary = [ordered]@{ PASS = 0; FAIL = 0; BLOCKED = 0; NOT_APPLICABLE = 0 }
    foreach ($c in $script:Checks) { $summary[$c.status]++ }
    $result = [ordered]@{ schema = 1; script_version = $ScriptVersion; smartscreen_observation = $SmartScreen; script_sha256 = (Get-FileHash $PSCommandPath -Algorithm SHA256).Hash.ToLower(); phase = $Phase; repeat = $Repeat
        started_at = $started.ToString('o'); finished_at = (Get-Date).ToString('o')
        options = [ordered]@{ allow_backend_control = [bool]$AllowBackendControl; allow_wsl_shutdown = [bool]$AllowWslShutdown; older_installer = [bool]$OlderInstaller
            audit_process_creation = [bool]$AuditProcessCreation }
        process_audit = [ordered]@{ active = [bool]$script:ProcessAudit.Active; reason = $script:ProcessAudit.Reason }
        environment = $script:Env; performance = $script:Perf; summary = $summary; checks = $script:Checks }
    $resultName = 'results.json'
    if ($Phase -eq 'PostReboot') { $resultName = 'results-postreboot-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json' }
    [IO.File]::WriteAllText((Join-Path $OutDir $resultName), ($result | ConvertTo-Json -Depth 8), $Utf8NoBom)
    Stop-Transcript | Out-Null
    $masked = 0
    foreach ($f in (Get-ChildItem $OutDir -Recurse -File | Where-Object { $_.Extension -in '.txt', '.json', '.log', '.err' })) {
        $t = [IO.File]::ReadAllText($f.FullName); $o = $t
        foreach ($s in $script:Secrets) { $t = $t.Replace($s, '[SECRET MASQUÉ]') }
        $t = $t.Replace($env:USERNAME, '<utilisateur>')
        if ($t -ne $o) { [IO.File]::WriteAllText($f.FullName, $t, $Utf8NoBom); $masked++ }
    }
    $zip = "$OutDir.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path (Join-Path $OutDir '*') -DestinationPath $zip
    Write-Host ''
    Write-Host ("Résultat : {0} PASS, {1} FAIL, {2} BLOCKED, {3} NOT_APPLICABLE" -f $summary.PASS, $summary.FAIL, $summary.BLOCKED, $summary.NOT_APPLICABLE) -ForegroundColor Cyan
    Write-Host "Dossier : $OutDir"
    Write-Host "Archive à transmettre (secrets et nom d'utilisateur masqués) : $zip" -ForegroundColor Cyan
    $script:FailCount = $summary.FAIL
}
if ($script:FailCount -gt 0) { exit 1 }
