#Requires -Version 5.1
<#
.SYNOPSIS
  Build OpsContent.exe (Windows desktop client) with PyInstaller.
#>
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

$Py = Get-PythonExe
Write-Host "[build] python: $($Py.Exe) $($Py.Prefix -join ' ')" -ForegroundColor Cyan

function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Args)
    $all = @($Py.Prefix) + $Args
    & $Py.Exe @all
    if ($LASTEXITCODE -ne 0) { throw "python failed: $($Args -join ' ') (exit=$LASTEXITCODE)" }
}

# Prefer local ops-content HTTP bridge if it is up (corporate nets block PyPI).
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect('127.0.0.1', 1088)
    if ($tcp.Connected) {
        $env:HTTP_PROXY = 'http://127.0.0.1:1088'
        $env:HTTPS_PROXY = 'http://127.0.0.1:1088'
        $env:http_proxy = $env:HTTP_PROXY
        $env:https_proxy = $env:HTTPS_PROXY
        Write-Host "[build] pip via local bridge :1088" -ForegroundColor Cyan
    }
    $tcp.Close()
} catch {}

Write-Host "[build] installing desktop deps..." -ForegroundColor Cyan
Invoke-Python -Args @('-m', 'pip', 'install', '--timeout', '60', '-r', 'requirements-desktop.txt')

$ico = Join-Path $Root 'desktop\app_icon.ico'
if (-not (Test-Path $ico)) {
    Write-Host "[build] generating icon..." -ForegroundColor Cyan
    $code = @"
from PIL import Image, ImageDraw
from pathlib import Path
p = Path(r'$($ico.Replace('\','\\'))')
img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.ellipse((16, 16, 240, 240), fill=(61, 122, 90, 255))
d.rectangle((112, 64, 144, 192), fill=(30, 30, 30, 255))
d.rectangle((72, 112, 184, 144), fill=(30, 30, 30, 255))
img.save(p, format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
"@
    Invoke-Python -Args @('-c', $code)
}

Write-Host "[build] pyinstaller..." -ForegroundColor Cyan
$dist = Join-Path $Root 'dist'
$work = Join-Path $Root 'build\pyi'
New-Item -ItemType Directory -Force -Path $dist, $work | Out-Null

Invoke-Python -Args @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--distpath', $dist,
    '--workpath', $work,
    (Join-Path $Root 'desktop\opscontent.spec')
)

$exe = Join-Path $dist 'OpsContent.exe'
if (-not (Test-Path $exe)) { throw "Missing $exe" }
Write-Host "[build] OK: $exe" -ForegroundColor Green
Write-Host "Put config.json, .env and SSH key next to the exe (or run from this repo)." -ForegroundColor Cyan
