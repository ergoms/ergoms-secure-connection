#Requires -Version 5.1
param(
    [Parameter(Position = 0)]
    [ValidateSet('set-token', 'deploy', 'whoami', 'test-proxy')]
    [string]$Command = 'deploy',

    [string]$Token = ''
)

$ErrorActionPreference = 'Stop'
$ModeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ModeDir '..\..')).Path

Write-Host '[vps] No automated deploy from office PC (Squid blocks :22; :443 must already listen).' -ForegroundColor Yellow
Write-Host ''
Write-Host '1) On VPS (provider console / VNC), run as root:'
Write-Host "   bash $(Join-Path $ModeDir 'bootstrap_sshd_443.sh')"
Write-Host '   # or copy modes/vps/bootstrap_sshd_443.sh to the server'
Write-Host ''
Write-Host '2) Optional HTTPS git relay (Caddy + github_proxy.py):'
Write-Host "   python3 $(Join-Path $ModeDir 'github_proxy.py') --port 8080"
Write-Host "   # TLS: $(Join-Path $ModeDir 'Caddyfile.example')"
Write-Host '   # config.json -> worker_base_url = https://git-proxy.YOUR_DOMAIN'
Write-Host ''
Write-Host '3) On office PC (.env MODE=socks, ssh.port=443 in config.json):'
Write-Host '   .\ops-content.ps1 probe YOUR_VPS_IP 443'
Write-Host '   .\ops-content.ps1 on'
Write-Host ''
if ($Command -ne 'deploy') {
    Write-Host "(command '$Command' is a no-op for vps mode)" -ForegroundColor DarkGray
}
