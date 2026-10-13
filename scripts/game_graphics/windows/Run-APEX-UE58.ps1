#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$EngineRoot,
    [string]$PythonExe,
    [string]$CriteriaFile,
    [switch]$PreflightOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Json([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 30), (New-Object Text.UTF8Encoding($false)))
}

function Assert-SafePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -notmatch '^[A-Za-z]:\\' -or $full -match '[~";\r\n]' -or
        $full -match '(?:^|\\)[^\\]*[. ](?:\\|$)' -or
        $full -match '(?i)^C:\\GameProject(?:\\|$)') {
        throw 'Protected, network or unsafe path refused. Use a normal local TEMP folder outside C:\GameProject.'
    }
    if ($null -eq ('ApexNativePaths' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class ApexNativePaths {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint QueryDosDevice(string device, StringBuilder target, int size);
}
'@
    }
    $device = New-Object Text.StringBuilder 4096
    if ([ApexNativePaths]::QueryDosDevice($full.Substring(0, 2), $device, $device.Capacity) -eq 0 -or
        $device.ToString().StartsWith('\??\') -or
        $device.ToString().Contains('LanmanRedirector')) {
        throw 'Unresolved, SUBST or mapped network drive refused.'
    }
    $current = $full
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse point refused: $current"
            }
        }
        $parent = [IO.Directory]::GetParent($current)
        if ($null -eq $parent) { break }
        $current = $parent.FullName
    }
    return $full
}

function Find-UE58([string]$ExplicitRoot) {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    if ($ExplicitRoot) { $candidates.Add($ExplicitRoot) }
    else {
        if ($env:APEX_UE_ROOT) { $candidates.Add($env:APEX_UE_ROOT) }
        foreach ($registry in @('HKLM:\SOFTWARE\EpicGames\Unreal Engine', 'HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds')) {
            if (-not (Test-Path -LiteralPath $registry)) { continue }
            if ($registry.EndsWith('Builds')) {
                $properties = (Get-ItemProperty -LiteralPath $registry).PSObject.Properties
                foreach ($property in $properties) {
                    if ($property.Name -notlike 'PS*' -and $property.Value -is [string]) { $candidates.Add($property.Value) }
                }
            } else {
                foreach ($key in Get-ChildItem -LiteralPath $registry) {
                    $property = Get-ItemProperty -LiteralPath $key.PSPath -Name InstalledDirectory -ErrorAction SilentlyContinue
                    if ($property -and $property.InstalledDirectory) { $candidates.Add($property.InstalledDirectory) }
                }
            }
        }
        $launcher = Join-Path $env:ProgramData 'Epic\UnrealEngineLauncher\LauncherInstalled.dat'
        if (Test-Path -LiteralPath $launcher) {
            foreach ($item in (Get-Content -LiteralPath $launcher -Raw | ConvertFrom-Json).InstallationList) {
                if ($item.AppName -like 'UE_5*') { $candidates.Add($item.InstallLocation) }
            }
        }
        foreach ($folder in Get-ChildItem -LiteralPath (Join-Path $env:ProgramFiles 'Epic Games') -Directory -ErrorAction SilentlyContinue) {
            if ($folder.Name -like 'UE_5*') { $candidates.Add($folder.FullName) }
        }
    }
    $valid = @()
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $versionFile = Join-Path $candidate 'Engine\Build\Build.version'
        if (-not (Test-Path -LiteralPath $versionFile)) { continue }
        $version = Get-Content -LiteralPath $versionFile -Raw | ConvertFrom-Json
        if ($version.MajorVersion -eq 5 -and $version.MinorVersion -eq 8 -and
            (Test-Path -LiteralPath (Join-Path $candidate 'Engine\Binaries\Win64\UnrealEditor.exe'))) {
            $valid += (Assert-SafePath $candidate)
        }
    }
    if ($valid.Count -eq 0) { throw 'UE5.8 not found. Re-run with -EngineRoot "D:\Epic Games\UE_5.8" using the actual installation.' }
    if ($valid.Count -gt 1) { throw 'Several UE5.8 installations found. Select one with -EngineRoot.' }
    return $valid[0]
}

function Probe-Python([string]$Executable, [string]$VersionPrefix = '') {
    # Probe a real executable with a bounded process; stderr from an absent py version is not a PS5 fatal error.
    $process = New-Object Diagnostics.Process
    $process.StartInfo = New-Object Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $Executable
    $process.StartInfo.Arguments = $VersionPrefix + ' -I -c "import sys,struct,venv,ensurepip; assert (3,11)<=sys.version_info[:2]<=(3,13) and struct.calcsize(''P'')==8; print(sys.executable)"'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(10000)) { $process.Kill(); $process.WaitForExit(); return $null }
        if ($process.ExitCode -eq 0) { return $stdout.Result.Trim() }
    } catch { return $null }
    finally { $process.Dispose() }
    return $null
}

function Find-Python([string]$ExplicitPython, [string]$UE) {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    if ($ExplicitPython) { $candidates.Add($ExplicitPython) }
    else {
        $candidates.Add((Join-Path $UE 'Engine\Binaries\ThirdParty\Python3\Win64\python.exe'))
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            foreach ($version in @('-3.12', '-3.11', '-3.13')) {
                $found = Probe-Python $launcher.Source $version
                if ($found) { $candidates.Add($found) }
            }
        }
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($python -and $python.Source -notlike '*WindowsApps*') { $candidates.Add($python.Source) }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $result = Probe-Python $candidate
        if ($result) { return $result }
    }
    throw 'Python x64 3.11-3.13 with venv/ensurepip unavailable, including the UE copy. Install Python 3.12 x64 from python.org and rerun (or pass -PythonExe). No engine files were modified.'
}

$runRoot = $null
$mutex = $null
$ownsMutex = $false
$exitCode = 2
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Write-Output 'BLOCKED: this launcher requires actual Windows; no Unreal process started.'
    exit 2
}
try {
    $bundle = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
    $null = Assert-SafePath $bundle
    $manifestPath = Join-Path $bundle 'bundle-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'Use the extracted APEX-UE58-validation.zip bundle; do not run an archived cloud project.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($entry in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $bundle $entry.path))
        if (-not $path.StartsWith($bundle + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Bundle path outside root.' }
        $null = Assert-SafePath $path
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Bundle checksum mismatch: $($entry.path)" }
    }
    $mutex = New-Object Threading.Mutex($false, 'Local\APEX_UE58_Qualification')
    $ownsMutex = $mutex.WaitOne(0)
    if (-not $ownsMutex) { throw 'Another qualification launcher is active; it remains untouched.' }
    $tempRoot = Assert-SafePath ([IO.Path]::GetTempPath())
    $runRoot = Join-Path $tempRoot ('APEX-UE58-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $runRoot
    Write-Json (Join-Path $runRoot 'launcher-owner.json') @{kind='APEX-UE58-qualification'; run_root=$runRoot; nonce=[Guid]::NewGuid().ToString()}
    Write-Host "APEX output: $runRoot"
    Write-Host 'Fresh project only. Keep this folder for inspection; no automatic deletion or upload.'
    $hardware = @{os=Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,TotalVisibleMemorySize,FreePhysicalMemory;
                  cpu=Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors;
                  video=Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status;
                  collected_at_utc=[DateTime]::UtcNow.ToString('o')}
    Write-Json (Join-Path $runRoot 'windows-hardware.json') $hardware
    $ue = Find-UE58 $EngineRoot
    foreach ($plugin in @('Engine\Plugins\Experimental\PythonScriptPlugin\PythonScriptPlugin.uplugin',
                           'Engine\Plugins\Editor\EditorScriptingUtilities\EditorScriptingUtilities.uplugin')) {
        if (-not (Test-Path -LiteralPath (Join-Path $ue $plugin))) { throw "Required UE plugin missing: $plugin" }
    }
    $python = Find-Python $PythonExe $ue
    Write-Json (Join-Path $runRoot 'preflight.json') @{status='prerequisites_found'; engine_root=$ue; python=$python; engine_executed=$false; api_qualified=$false}
    if ($PreflightOnly) {
        Write-Host 'Preflight complete; Unreal was not executed. Evidence in the output folder.'
        $exitCode = 0
    } else {
        $venv = Join-Path $runRoot 'host-venv'
        & $python -I -m venv $venv *> (Join-Path $runRoot 'venv.log')
        if ($LASTEXITCODE -ne 0) { throw 'Temporary venv creation failed; see venv.log. UE installation untouched.' }
        $hostPython = Join-Path $venv 'Scripts\python.exe'
        Write-Host 'Installing three pinned host libraries into the temporary venv (about 21 MB, PyPI HTTPS).'
        & $hostPython -I -m pip --isolated install --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes --index-url https://pypi.org/simple -r (Join-Path $PSScriptRoot 'requirements.txt') *> (Join-Path $runRoot 'dependencies.log')
        if ($LASTEXITCODE -ne 0) { throw 'Host dependency install failed; see dependencies.log. No Unreal process started.' }
        $arguments = @('--run-root', $runRoot, '--engine-root', $ue)
        if ($CriteriaFile) {
            $criteriaCopy = Join-Path $runRoot 'requested-criteria.json'
            Copy-Item -LiteralPath $CriteriaFile -Destination $criteriaCopy
            $arguments += @('--criteria', $criteriaCopy)
        }
        # -I ignores PYTHONPATH/user-site. Add only this integrity-checked bundle for the existing APEX judge.
        $entrypoint = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('apex.game_graphics.ue.windows_validation',run_name='__main__')"
        & $hostPython -I -c $entrypoint $bundle @arguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0 -and -not (Test-Path -LiteralPath (Join-Path $runRoot 'validation-report.json'))) {
            throw 'Qualification host stopped before a complete report; bootstrap captures the failure.'
        }
    }
} catch {
    $reason = $_.Exception.Message
    Write-Host "BLOCKED: $reason"
    if ($runRoot) {
        $started = @(Get-ChildItem -LiteralPath $runRoot -Recurse -Filter '*-started.json' -File -ErrorAction SilentlyContinue)
        Write-Json (Join-Path $runRoot 'bootstrap-error.json') @{status='blocked'; reason=$reason; engine_executed=($started.Count -gt 0)}
        $small = @(Get-ChildItem -LiteralPath $runRoot -File)
        if ($small.Count -gt 0 -and -not (Test-Path -LiteralPath ($runRoot + '.zip'))) {
            Compress-Archive -LiteralPath $small.FullName -DestinationPath ($runRoot + '.zip')
        }
    }
    $exitCode = 2
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    if ($runRoot) { Write-Host "Results preserved: $runRoot" }
}
exit $exitCode
