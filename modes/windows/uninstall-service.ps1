#Requires -Version 5.1
<#
.SYNOPSIS
  Remove the Windows service installed by modes/windows/install-service.ps1
#>
param(
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'
$ServiceId = 'ops-content'

if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = [Security.Principal.WindowsPrincipal]$id
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    $argList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-Root', $Root
    )
    try {
        $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -Verb RunAs -Wait -PassThru -ArgumentList $argList
        exit $(if ($p) { $p.ExitCode } else { 1 })
    }
    catch {
        Write-Error "Administrator rights required: $_"
        exit 1
    }
}

Set-Location $Root
$winsw = Join-Path $Root 'tools\ops-content-service.exe'
$xmlPath = Join-Path $Root 'tools\ops-content-service.xml'

if (Test-Path -LiteralPath $winsw) {
    & $winsw stop 2>$null | Out-Null
    Start-Sleep -Seconds 1
    & $winsw uninstall 2>$null | Out-Null
}

$svc = Get-Service -Name $ServiceId -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    sc.exe delete $ServiceId | Out-Null
}

Remove-Item -LiteralPath $xmlPath -ErrorAction SilentlyContinue

Write-Host "Removed $ServiceId"
