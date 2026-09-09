#Requires -Version 5.1
param(
    [ValidateSet('setup', 'build', 'all', 'libraries', 'sing-box', 'pyinstaller', 'exe', 'installer')]
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
    # Native stdout must not enter the function success stream - callers
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

function Repair-ProjectVenv {
    $venvDir = Join-Path $Root '.venv'
    $cfg = Join-Path $venvDir 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $cfg)) { return }
    $command = $null
    foreach ($line in Get-Content -LiteralPath $cfg) {
        if ($line -match '^\s*command\s*=\s*(.+)$') {
            $command = $Matches[1].Trim()
            break
        }
    }
    if (-not $command) { return }
    $expected = (Join-Path $Root '.venv').TrimEnd('\')
    $cmdNorm = $command.Replace('/', '\').TrimEnd('\').ToLowerInvariant()
    $expNorm = $expected.ToLowerInvariant()
    if ($cmdNorm -like ('*' + $expNorm + '*')) { return }
    Write-Host "[ERGOMS SECURE CONNECTION] .venv belongs to another project - recreating"
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}

function Get-VenvPython {
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        throw '.venv not found - run ERGOMS SECURE CONNECTION: setup first'
    }
    return $py
}

function Install-Libraries {
    Repair-ProjectVenv
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
    $py = Get-VenvPython
    & $py -m desktop download-sing-box
    if ($LASTEXITCODE -ne 0) { throw "download-sing-box failed: $LASTEXITCODE" }
}

function Invoke-PyInstaller {
    Repair-ProjectVenv
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
    $py = Get-VenvPython
    Write-Host 'PyInstaller: ErgomsSecureConnection.spec -> dist/ErgomsSecureConnection/'
    & $py -m PyInstaller --noconfirm --clean ErgomsSecureConnection.spec
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed: $LASTEXITCODE" }
    $exe = Join-Path $Root 'dist\ErgomsSecureConnection\ErgomsSecureConnection.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "missing $exe" }
    Write-Host "OK: $exe"
}

function Get-AppVersion {
    $init = Join-Path $Root 'desktop\__init__.py'
    foreach ($line in Get-Content -LiteralPath $init) {
        if ($line -match '__version__\s*=\s*"([^"]+)"') {
            return $Matches[1]
        }
    }
    return '1.0.0'
}

function Get-IsccExe {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

function Get-WingetExe {
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }
    $apps = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
    if (Test-Path -LiteralPath $apps) { return $apps }
    return $null
}

function Install-InnoSetup {
    Write-Host 'Inno Setup not found - installing via winget...'
    $winget = Get-WingetExe
    if (-not $winget) {
        throw 'Inno Setup 6 (ISCC.exe) not found and winget is unavailable'
    }
    & $winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "winget Inno Setup failed: $LASTEXITCODE" }
    $iscc = Get-IsccExe
    if (-not $iscc) { throw 'Inno Setup installed but ISCC.exe not found' }
    return $iscc
}

function Invoke-Installer {
    $exe = Join-Path $Root 'dist\ErgomsSecureConnection\ErgomsSecureConnection.exe'
    if (-not (Test-Path -LiteralPath $exe)) {
        throw 'missing dist/ErgomsSecureConnection/ErgomsSecureConnection.exe — run build first'
    }
    $iscc = Get-IsccExe
    if (-not $iscc) {
        Install-InnoSetup | Out-Null
        $iscc = Get-IsccExe
    }
    if (-not $iscc) { throw 'ISCC.exe not found after Inno Setup install' }
    $ver = Get-AppVersion
    $iss = Join-Path $Root 'installer\ErgomsSecureConnection.iss'
    Write-Host "Inno Setup: $iscc /DAppVersion=$ver"
    & $iscc "/DAppVersion=$ver" $iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }
    $setup = Join-Path $Root "dist\ErgomsSecureConnection-Setup-$ver.exe"
    if (-not (Test-Path -LiteralPath $setup)) { throw "missing $setup" }
    Write-Host "OK: $setup"
}

switch ($Target) {
    'libraries' { Install-Libraries }
    'sing-box' { Install-SingBox }
    'pyinstaller' { Invoke-PyInstaller }
    'installer' { Invoke-Installer }
    { $_ -in @('setup', 'all') } { Install-Libraries; Install-SingBox }
    { $_ -in @('build', 'exe') } { Install-Libraries; Install-SingBox; Invoke-PyInstaller; Invoke-Installer }
}
