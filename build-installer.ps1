#Requires -Version 5.1
<#
.SYNOPSIS
  Build OpsContent.exe and a Windows Setup installer (Inno Setup).

.OUTPUTS
  dist\OpsContent.exe
  dist\OpsContent-Setup-1.0.0.exe
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host '[installer] building desktop exe...' -ForegroundColor Cyan
& (Join-Path $Root 'build-desktop.ps1')
if ($LASTEXITCODE -ne 0) { throw "build-desktop.ps1 failed (exit=$LASTEXITCODE)" }

$exe = Join-Path $Root 'dist\OpsContent.exe'
if (-not (Test-Path $exe)) { throw "Missing $exe" }

function Find-ISCC {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
        )) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

$iscc = Find-ISCC
if (-not $iscc) {
    Write-Host '[installer] Inno Setup not found — installing via winget...' -ForegroundColor Yellow
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    $iscc = Find-ISCC
    if (-not $iscc) {
        throw 'ISCC.exe not found after winget install. Install Inno Setup 6+ and re-run.'
    }
}

Write-Host ('[installer] ISCC: ' + $iscc) -ForegroundColor Cyan
$iss = Join-Path $Root 'installer\opscontent.iss'
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit=$LASTEXITCODE)" }

$setup = Get-ChildItem (Join-Path $Root 'dist') -Filter 'OpsContent-Setup-*.exe' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $setup) { throw 'Setup exe not produced in dist\' }

Write-Host ('[installer] OK: ' + $setup.FullName) -ForegroundColor Green
Write-Host 'Install puts the app in Program Files; config goes to %LOCALAPPDATA%\ops-content' -ForegroundColor Cyan
