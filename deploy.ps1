#Requires -Version 5.1
param(
    [Parameter(Position = 0)]
    [ValidateSet('deploy')]
    [string]$Command = 'deploy'
)

$ErrorActionPreference = 'Stop'

Write-Host '[deploy] VPS: одна команда с консоли хостинга (root)' -ForegroundColor Cyan
Write-Host '  curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/vps/install.sh | bash'
Write-Host 'Если репозиторий уже на сервере:'
Write-Host '  sudo bash modes/vps/install.sh'
Write-Host 'Только Reality, без AmneziaWG:  sudo bash modes/vps/install.sh --no-awg'
Write-Host 'На ПК: Настройки → Из файла  (client.json с VPS: /var/lib/ops-content-singbox/client.json)'
exit 0
