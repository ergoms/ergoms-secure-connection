#Requires -Version 5.1
<#
.SYNOPSIS
  Thin wrapper to unified Python client (CLI)
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

if ($Command -eq 'deploy') {
    & (Join-Path $Root 'deploy.ps1') @Rest
    exit $LASTEXITCODE
}

if ($Command -in @('install-service', 'uninstall-service')) {
    $script = Join-Path $Root "modes\windows\$Command.ps1"
    if (-not (Test-Path -LiteralPath $script)) {
        Write-Error "missing $script"
        exit 1
    }
    & $script
    exit $LASTEXITCODE
}

$Py = Get-PythonExe
$env:PYTHONPATH = $Root
$argsList = @($Py.Prefix) + @('-m', 'desktop', $Command) + @($Rest)
& $Py.Exe @argsList
exit $LASTEXITCODE
