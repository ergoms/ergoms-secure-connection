#Requires -Version 5.1
<#
.SYNOPSIS
  Thin wrapper → unified Python client (same commands as Linux / EXE)
#>
param(
    [Parameter(Position = 0)]
    [string]$Command = 'help',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Get-PythonExe {
    foreach ($name in @('python', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -match 'WindowsApps') { continue }
        if ($name -eq 'py') {
            & $cmd.Source -3 -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return @{ Exe = $cmd.Source; Prefix = @('-3') } }
        }
        else {
            & $cmd.Source -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return @{ Exe = $cmd.Source; Prefix = @() } }
        }
    }
    throw 'Python 3 not found'
}

# Legacy deploy stays in PowerShell
if ($Command -eq 'deploy') {
    & (Join-Path $Root 'deploy.ps1') @Rest
    exit $LASTEXITCODE
}

if ($Command -in @('install-service', 'uninstall-service')) {
    Write-Host '[ops-content] systemd service is Linux-only: ./ops-content.sh install-service' -ForegroundColor Yellow
    exit 0
}

$Py = Get-PythonExe
$env:PYTHONPATH = $Root
$argsList = @($Py.Prefix) + @('-m', 'desktop', $Command) + @($Rest)
& $Py.Exe @argsList
exit $LASTEXITCODE
