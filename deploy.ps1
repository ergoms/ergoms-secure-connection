#Requires -Version 5.1
param(
    [Parameter(Position = 0)]
    [ValidateSet('deploy')]
    [string]$Command = 'deploy'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host '[deploy] VLESS+Reality (sing-box) on VPS :443' -ForegroundColor Cyan
Write-Host 'On VPS (console):'
Write-Host '  bash modes/vps/disable_sshd_443.sh'
Write-Host '  bash modes/vps/bootstrap_singbox_443.sh'
Write-Host 'On PC: paste transport into config.json (server.host = VPS IP)'
Write-Host '  .\ergoms-secure-connection.ps1 probe HOST 443'
Write-Host '  .\ergoms-secure-connection.ps1 on'
exit 0
