#Requires -Version 5.1
param(
    [ValidateSet('all', 'libraries', 'sing-box')]
    [string]$Target = 'all'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

function Get-SystemPython {
    foreach ($name in @('python', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -match 'WindowsApps') { continue }
        if ($name -eq 'py') {
            & $cmd.Source -3 -c 'import sys' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ Exe = $cmd.Source; Prefix = @('-3') }
            }
        }
        else {
            & $cmd.Source -c 'import sys' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ Exe = $cmd.Source; Prefix = @() }
            }
        }
    }
    throw 'Python 3 not found (need 3.10-3.14)'
}

function Get-PoetryExe {
    $env:Path = "$(Join-Path $env:APPDATA 'Python\Scripts');$env:Path"
    $candidates = @(
        (Join-Path $env:APPDATA 'Python\Scripts\poetry.exe')
    )
    $cmd = Get-Command poetry -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

function Install-Poetry {
    Write-Host 'Poetry not found - installing...'
    $py = Get-SystemPython
    $installer = Join-Path $env:TEMP 'install-poetry.py'
    $fetch = 'import urllib.request; urllib.request.urlretrieve("https://install.python-poetry.org", r"{0}")' -f $installer
    & $py.Exe @($py.Prefix + @('-c', $fetch))
    if ($LASTEXITCODE -ne 0) { throw "Failed to download Poetry installer: $LASTEXITCODE" }
    & $py.Exe @($py.Prefix + @($installer))
    if ($LASTEXITCODE -ne 0) { throw "Poetry installer failed: $LASTEXITCODE" }
    $poetry = Get-PoetryExe
    if (-not $poetry) { throw 'Poetry install failed' }
    return $poetry
}

function Install-Libraries {
    $poetry = Get-PoetryExe
    if (-not $poetry) { $poetry = Install-Poetry }
    Write-Host "Poetry: $poetry"
    & $poetry install --extras gui
    if ($LASTEXITCODE -ne 0) { throw "poetry install failed: $LASTEXITCODE" }
}

function Install-SingBox {
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        throw '.venv not found - run ops-content: install libraries first'
    }
    & $py -m desktop download-sing-box
    if ($LASTEXITCODE -ne 0) { throw "download-sing-box failed: $LASTEXITCODE" }
}

switch ($Target) {
    'libraries' { Install-Libraries }
    'sing-box' { Install-SingBox }
    'all' { Install-Libraries; Install-SingBox }
}
