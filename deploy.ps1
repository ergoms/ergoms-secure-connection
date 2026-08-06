#Requires -Version 5.1
param(
    [Parameter(Position = 0)]
    [ValidateSet('deploy')]
    [string]$Command = 'deploy'
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
    Write-Host "[deploy] MODE=socks — open SSH on VPS :443, then CLI client on." -ForegroundColor Yellow
    Write-Host 'On VPS (console): bash modes/vps/bootstrap_sshd_443.sh'
    Write-Host 'On Linux: ./ops-content.sh probe HOST 443 ; ./ops-content.sh on'
    Write-Host 'On Windows: .\ops-content.ps1 probe HOST 443 ; .\ops-content.ps1 on'
    Write-Host '  (or: python -m desktop on)'
    exit 0
}

if ($Mode -eq 'singbox') {
    Write-Host "[deploy] MODE=singbox — sing-box VLESS+Reality on VPS :443; CLI client on." -ForegroundColor Yellow
    Write-Host 'On VPS (console):'
    Write-Host '  bash modes/vps/disable_sshd_443.sh'
    Write-Host '  bash modes/vps/bootstrap_singbox_443.sh'
    Write-Host 'On PC: paste transport into config.json; set MODE=singbox TUN=1 in .env'
    Write-Host '  .\ops-content.ps1 on   or   python -m desktop on'
    Write-Host '  probe: .\ops-content.ps1 probe HOST 443'
    exit 0
}

throw "Unknown MODE=$Mode (use socks|singbox). See config/.env.example"
