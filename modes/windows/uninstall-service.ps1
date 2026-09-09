#Requires -Version 5.1
<#
.SYNOPSIS
  Remove the Windows service installed by modes/windows/install-service.ps1
#>
param(
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'
$ServiceIds = @('ergoms-vpn', 'ops-content')

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
$winswCandidates = @(
    (Join-Path $Root 'tools\ergoms-vpn-service.exe')
    (Join-Path $Root 'tools\ops-content-service.exe')
)
$xmlCandidates = @(
    (Join-Path $Root 'tools\ergoms-vpn-service.xml')
    (Join-Path $Root 'tools\ops-content-service.xml')
)

foreach ($winsw in $winswCandidates) {
    if (Test-Path -LiteralPath $winsw) {
        & $winsw stop 2>$null | Out-Null
        Start-Sleep -Seconds 1
        & $winsw uninstall 2>$null | Out-Null
    }
}

foreach ($id in $ServiceIds) {
    $svc = Get-Service -Name $id -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -ne 'Stopped') {
            Stop-Service -Name $id -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
        sc.exe delete $id | Out-Null
        Write-Host "Removed $id"
    }
}

foreach ($xmlPath in $xmlCandidates) {
    Remove-Item -LiteralPath $xmlPath -ErrorAction SilentlyContinue
}
