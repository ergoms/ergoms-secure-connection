#Requires -Version 5.1
param(
    [ValidateSet('setup', 'build', 'all', 'libraries', 'sing-box', 'pyinstaller', 'exe')]
    [string]$Target = 'setup'
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
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri 'https://install.python-poetry.org' -OutFile $installer -UseBasicParsing
    }
    catch {
        & curl.exe -fsSL 'https://install.python-poetry.org' -o $installer
        if ($LASTEXITCODE -ne 0) { throw "Failed to download Poetry installer: $_" }
    }
    if (-not (Test-Path -LiteralPath $installer) -or (Get-Item -LiteralPath $installer).Length -lt 100) {
        throw 'Failed to download Poetry installer'
    }
    # Native stdout must not enter the function success stream — callers
    # assign the return value to $poetry.
    & $py.Exe @($py.Prefix + @($installer)) | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Poetry installer failed: $LASTEXITCODE" }
    $poetry = Get-PoetryExe
    if (-not $poetry) { throw 'Poetry install failed' }
    return $poetry
}

function Get-SystemPythonExe {
    $py = Get-SystemPython
    $out = & $py.Exe @($py.Prefix + @('-c', 'import sys; print(sys.executable)')) 2>$null
    $exe = @($out) | Where-Object { $_ } | Select-Object -Last 1
    if (-not $exe -or $exe -match 'WindowsApps' -or -not (Test-Path -LiteralPath $exe)) {
        throw 'Python 3 not found (need 3.10-3.14, not the Microsoft Store stub)'
    }
    return $exe
}

function Use-RealPythonPath {
    $pyExe = Get-SystemPythonExe
    $pyDir = Split-Path -Parent $pyExe
    $parts = @(
        $pyDir
        (Join-Path $pyDir 'Scripts')
        (Join-Path $env:APPDATA 'Python\Scripts')
    )
    $parts += $env:Path -split ';' | Where-Object { $_ -and $_ -notmatch 'WindowsApps' }
    $env:Path = ($parts | Select-Object -Unique) -join ';'
    $env:VIRTUAL_ENV = $null
    return $pyExe
}

function Install-Libraries {
    $poetry = Get-PoetryExe
    if (-not $poetry) { $poetry = Install-Poetry }
    $pyExe = Use-RealPythonPath
    Write-Host "Poetry: $poetry"
    Write-Host "Python: $pyExe"
    & $poetry env use -- $pyExe
    if ($LASTEXITCODE -ne 0) { throw "poetry env use failed: $LASTEXITCODE" }
    & $poetry install --extras gui
    if ($LASTEXITCODE -ne 0) { throw "poetry install failed: $LASTEXITCODE" }
}

function Install-SingBox {
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        throw '.venv not found - run ops-content: setup first'
    }
    & $py -m desktop download-sing-box
    if ($LASTEXITCODE -ne 0) { throw "download-sing-box failed: $LASTEXITCODE" }
}

function Invoke-PyInstaller {
    $poetry = Get-PoetryExe
    if (-not $poetry) { $poetry = Install-Poetry }
    Use-RealPythonPath | Out-Null
    & $poetry env use -- (Get-SystemPythonExe)
    if ($LASTEXITCODE -ne 0) { throw "poetry env use failed: $LASTEXITCODE" }
    & $poetry install --extras gui --extras build
    if ($LASTEXITCODE -ne 0) { throw "poetry install failed: $LASTEXITCODE" }
    $sb = Join-Path $Root 'tools\sing-box.exe'
    if (-not (Test-Path -LiteralPath $sb)) {
        Install-SingBox
    }
    Write-Host 'PyInstaller: OpsContent.spec -> dist/OpsContent.exe'
    & $poetry run pyinstaller --noconfirm --clean OpsContent.spec
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed: $LASTEXITCODE" }
    $exe = Join-Path $Root 'dist\OpsContent.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "missing $exe" }
    Write-Host "OK: $exe"
}

switch ($Target) {
    'libraries' { Install-Libraries }
    'sing-box' { Install-SingBox }
    'pyinstaller' { Invoke-PyInstaller }
    { $_ -in @('setup', 'all') } { Install-Libraries; Install-SingBox }
    { $_ -in @('build', 'exe') } { Install-Libraries; Install-SingBox; Invoke-PyInstaller }
}
