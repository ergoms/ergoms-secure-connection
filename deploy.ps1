#Requires -Version 5.1
param(
    [Parameter(Position = 0)]
    [ValidateSet('set-token', 'deploy', 'whoami', 'test-proxy')]
    [string]$Command = 'deploy',

    [string]$Token = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$CredsDir = Join-Path $Root 'creds'
$EnvPath = Join-Path $Root '.env'
$EnvLegacyPath = Join-Path $CredsDir '.env'

function Import-DotEnv([string]$Path) {
    New-Item -ItemType Directory -Force -Path $CredsDir | Out-Null
    if (-not (Test-Path $Path)) {
        if (Test-Path $EnvLegacyPath) { $Path = $EnvLegacyPath } else { return }
    }
    Get-Content -Path $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $i = $line.IndexOf('=')
        if ($i -lt 1) { return }
        $key = $line.Substring(0, $i).Trim()
        $val = $line.Substring($i + 1).Trim().Trim('"').Trim("'")
        if ($key) { Set-Item -Path "Env:$key" -Value $val }
    }
}

Import-DotEnv $EnvPath

$Mode = if ($env:MODE) { $env:MODE.Trim().ToLowerInvariant() } else { 'socks' }

if ($Mode -eq 'socks') {
    Write-Host "[deploy] MODE=socks — open SSH on VPS :443, then client on." -ForegroundColor Yellow
    Write-Host 'On VPS (console): bash modes/vps/bootstrap_sshd_443.sh'
    Write-Host 'On PC: .\ops-content.ps1 probe HOST 443 ; .\ops-content.ps1 on'
    exit 0
}

if ($Mode -eq 'vps') {
    $script = Join-Path $Root (Join-Path 'modes' (Join-Path 'vps' 'deploy.ps1'))
    Write-Host "[deploy] MODE=vps -> $script" -ForegroundColor Cyan
    & $script $Command -Token $Token
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        exit $LASTEXITCODE
    }
    exit 0
}

throw "Unknown MODE=$Mode (use socks|vps). See config/.env.example"
