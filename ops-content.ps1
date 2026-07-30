#Requires -Version 5.1
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        'init', 'start', 'stop', 'status', 'test', 'probe',
        'on', 'off', 'deploy',
        'relay-on', 'relay-off',
        'install-service', 'uninstall-service',
        'help'
    )]
    [string]$Command = 'help',

    [Parameter(Position = 1)]
    [string]$ProbeHost = '',

    [Parameter(Position = 2)]
    [int]$ProbePort = 443
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigDir = Join-Path $Root 'config'
$CredsDir = Join-Path $Root 'creds'
$LogsDir = Join-Path $Root 'logs'
$VarDir = Join-Path $Root 'var'
$ConfigPath = Join-Path $Root 'config.json'
$ConfigExamplePath = Join-Path $ConfigDir 'config.example.json'
$EnvExamplePath = Join-Path $ConfigDir '.env.example'
$EnvPath = Join-Path $Root '.env'
$EnvLegacyPath = Join-Path $CredsDir '.env'
$KnownHostsPath = Join-Path $CredsDir 'ssh_known_hosts'
$PidPath = Join-Path $VarDir 'ssh.pid'
$BridgePidPath = Join-Path $VarDir 'bridge.pid'
$StatePath = Join-Path $VarDir 'state.json'
$ProxyBackupPath = Join-Path $VarDir 'winproxy.bak.json'
$EnvExportPath = Join-Path $VarDir 'cli.env'
$ProxyCmdPath = Join-Path $VarDir 'proxy.cmd'
$ConnectPy = Join-Path $Root (Join-Path 'lib' 'connect_proxy.py')
$ProbePy = Join-Path $Root (Join-Path 'lib' 'probe_connect.py')
$HttpBridgePy = Join-Path $Root (Join-Path 'lib' 'http_via_socks.py')
$DeployPs1 = Join-Path $Root 'deploy.ps1'
$IsWindowsOs = ($env:OS -eq 'Windows_NT') -or ($IsWindows -eq $true)

function Move-IfLegacy([string]$Src, [string]$Dst, [string]$Label) {
    if ((Test-Path $Src) -and -not (Test-Path $Dst)) {
        $parent = Split-Path -Parent $Dst
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Move-Item -Force $Src $Dst
        Write-Warn "moved $Label"
    }
}

function Ensure-DataDirs {
    New-Item -ItemType Directory -Force -Path (Join-Path $CredsDir 'certs'), $LogsDir, $VarDir, $ConfigDir | Out-Null
    if (-not (Test-Path $KnownHostsPath)) {
        New-Item -ItemType File -Force -Path $KnownHostsPath | Out-Null
    }
    Move-IfLegacy $EnvLegacyPath $EnvPath 'creds/.env -> .env'
    foreach ($name in @('ssh-debug.log', 'ssh-debug.out')) {
        Move-IfLegacy (Join-Path $Root $name) (Join-Path $LogsDir $name) "$name -> logs/"
    }
    Move-IfLegacy (Join-Path $Root '.ops-content.ssh.pid') $PidPath 'ssh.pid -> var/'
    Move-IfLegacy (Join-Path $Root '.ops-content.bridge.pid') $BridgePidPath 'bridge.pid -> var/'
    Move-IfLegacy (Join-Path $Root '.ops-content.state.json') $StatePath 'state -> var/'
    Move-IfLegacy (Join-Path $Root '.ops-content.winproxy.bak.json') $ProxyBackupPath 'winproxy.bak -> var/'
    Move-IfLegacy (Join-Path $Root '.ops-content.linuxproxy.bak.json') (Join-Path $VarDir 'linuxproxy.bak.json') 'linuxproxy.bak -> var/'
    Move-IfLegacy (Join-Path $Root '.ops-content.proxy.cmd') $ProxyCmdPath 'proxy.cmd -> var/'
    Move-IfLegacy (Join-Path $Root '.ops-content.env') $EnvExportPath 'cli.env -> var/'
    Move-IfLegacy (Join-Path $Root '.env.example') $EnvExamplePath '.env.example -> config/'
    Move-IfLegacy (Join-Path $Root 'config.example.json') $ConfigExamplePath 'config.example.json -> config/'
    Move-IfLegacy (Join-Path $Root 'connect_proxy.py') $ConnectPy 'connect_proxy.py -> lib/'
    Move-IfLegacy (Join-Path $Root 'probe_connect.py') $ProbePy 'probe_connect.py -> lib/'
}

function Write-Info($msg) { Write-Host "[ops-content] $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "[ops-content] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[ops-content] $msg" -ForegroundColor Yellow }

function Import-DotEnv([string]$Path) {
    Ensure-DataDirs
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
        # .env is source of truth for this project (stale $env:MODE in the shell breaks mode switches)
        if ($key) { Set-Item -Path "Env:$key" -Value $val }
    }
}

Import-DotEnv $EnvPath

function Get-Mode {
    if ($env:MODE) { return $env:MODE.Trim().ToLowerInvariant() }
    return 'socks'
}

function Get-Config {
    if (-not (Test-Path $ConfigPath)) {
        throw "Missing config.json. Run: .\ops-content.ps1 init"
    }
    return Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
}

function Get-PythonCommand {
    # Prefer real interpreters; skip Microsoft Store stubs (WindowsApps, exit 9009).
    $candidates = @(
        @{ Name = 'python3'; Prefix = @() },
        @{ Name = 'python'; Prefix = @() },
        @{ Name = 'py'; Prefix = @('-3') }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -match 'WindowsApps') { continue }
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $cmd.Source @($c.Prefix + @('-c', 'import sys')) 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ Exe = $cmd.Source; Prefix = $c.Prefix }
            }
        } finally {
            $ErrorActionPreference = $prev
        }
    }
    throw 'Python not found (need working python3/python/py)'
}

function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$ArgList)
    $py = Get-PythonCommand
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $code = 0
    try {
        & $py.Exe @($py.Prefix + $ArgList) 2>&1 | ForEach-Object { Write-Host $_ }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($null -eq $code) { $code = 0 }
    return [int]$code
}

function Invoke-Init {
    Ensure-DataDirs
    if (-not (Test-Path $ConfigPath)) {
        Copy-Item $ConfigExamplePath $ConfigPath
        Write-Ok 'Created config.json'
    } else {
        Write-Info 'config.json already exists'
    }
    if (-not (Test-Path $EnvPath) -and (Test-Path $EnvExamplePath)) {
        Copy-Item $EnvExamplePath $EnvPath
        Write-Ok 'Created .env from config/.env.example'
    }
    Write-Host ''
    Write-Host '1) Edit config.json (ssh / corporate_proxy) and .env (MODE)'
    Write-Host '2) Deploy backend:  .\deploy.ps1   or  .\ops-content.ps1 deploy'
    Write-Host '3) Enable:          .\ops-content.ps1 on'
    Write-Host ''
    Write-Host 'Layout: .env | config.json | config/ examples | creds/ keys | logs/ | var/'
    Write-Host 'Modes: socks | vps'
}

function Format-ProxyCommandToken([string]$Token) {
    if ($Token -match '[\s"]') {
        return '"' + ($Token -replace '"', '\"') + '"'
    }
    return $Token
}

function Get-ProxyCommand([object]$cfg) {
    $py = Get-PythonCommand
    # Windows OpenSSH + Start-Process both break on spaces in ProxyCommand.
    # Use a tiny .cmd wrapper so ProxyCommand is a single path + %h %p.
    if ($IsWindowsOs) {
        $wrapper = $ProxyCmdPath
        $exe = $py.Exe
        $prefix = if ($py.Prefix.Count -gt 0) { ($py.Prefix -join ' ') + ' ' } else { '' }
        $allowPort = if ($cfg.ssh.port) { "$($cfg.ssh.port)" } else { '443' }
        Ensure-DataDirs
        @(
            '@echo off'
            "set OPS_CONTENT_HTTP_PROXY=$($cfg.corporate_proxy)"
            "set OPS_CONTENT_CONNECT_ALLOW=$($cfg.ssh.host):$allowPort"
            "`"$exe`" $prefix`"$ConnectPy`" %1 %2"
        ) -join "`r`n" | Set-Content -Path $wrapper -Encoding ASCII
        return "$(Format-ProxyCommandToken $wrapper) %h %p"
    }
    $tokens = @($py.Exe) + @($py.Prefix) + @($ConnectPy, '%h', '%p')
    return (($tokens | ForEach-Object { Format-ProxyCommandToken "$_" }) -join ' ')
}

function Get-SocksScope {
    $s = if ($env:SOCKS_SCOPE) { $env:SOCKS_SCOPE.Trim().ToLowerInvariant() } else { 'full' }
    if ($s -in @('full', 'all', 'system')) { return 'full' }
    if ($s -in @('github', 'pac', 'partial')) { return 'github' }
    return 'full'
}

function Get-SshArgs([object]$cfg) {
    Ensure-DataDirs
    $proxyCommand = Get-ProxyCommand $cfg
    $port = if ($cfg.ssh.port) { "$($cfg.ssh.port)" } else { '443' }
    # Restrict CONNECT shim to the configured VPS only
    $env:OPS_CONTENT_CONNECT_ALLOW = "$($cfg.ssh.host):$port"
    # OpenSSH on Windows accepts forward slashes in paths
    $kh = ($KnownHostsPath -replace '\\', '/')
    $gkh = 'NUL'
    $args = @(
        '-N',
        '-D', "127.0.0.1:$($cfg.ssh.local_socks_port)",
        '-p', $port,
        '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=20',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', "UserKnownHostsFile=$kh",
        '-o', "GlobalKnownHostsFile=$gkh",
        '-o', 'UpdateHostKeys=yes',
        '-o', 'Compression=no',
        '-o', 'IPQoS=throughput',
        '-o', 'TCPKeepAlive=yes',
        '-o', 'Ciphers=chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com,aes128-ctr',
        '-o', 'HostKeyAlgorithms=ssh-ed25519,rsa-sha2-512,rsa-sha2-256',
        '-o', "ProxyCommand=$proxyCommand"
    )
    if ($cfg.ssh.identity_file) {
        $args += @('-o', 'IdentitiesOnly=yes', '-i', $cfg.ssh.identity_file)
    }
    $args += "$($cfg.ssh.user)@$($cfg.ssh.host)"
    return $args
}

function Quote-WinProcessArg([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Start-SshProcess([string[]]$SshArgs) {
    if (-not $IsWindowsOs) {
        return Start-Process -FilePath 'ssh' -ArgumentList $SshArgs -PassThru
    }
    # PS 5.1 Start-Process joins ArgumentList with spaces and re-parses —
    # ProxyCommand=python script.py gets split. Use ProcessStartInfo instead.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'ssh'
    $psi.Arguments = (($SshArgs | ForEach-Object { Quote-WinProcessArg $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    if (-not $p.Start()) { throw 'failed to start ssh' }
    return $p
}

function Get-HttpBridgePort {
    if ($env:HTTP_BRIDGE_PORT) { return [int]$env:HTTP_BRIDGE_PORT }
    return 1088
}

function Stop-HttpBridge {
    if (Test-Path $BridgePidPath) {
        $oldPid = [int](Get-Content $BridgePidPath)
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
            Stop-Process -Id $oldPid -Force
            Write-Ok "http-bridge pid=$oldPid stopped"
        }
        Remove-Item $BridgePidPath -ErrorAction SilentlyContinue
    }
}

function Get-BridgeArgList([object]$cfg) {
    $socksPort = [int]$cfg.ssh.local_socks_port
    $httpPort = Get-HttpBridgePort
    $scope = Get-SocksScope
    $mode = if ($scope -eq 'full') { 'full' } else { 'github' }
    $argList = @(
        $HttpBridgePy,
        '--listen', "127.0.0.1:$httpPort",
        '--socks', "127.0.0.1:$socksPort",
        '--mode', $mode
    )
    if ($cfg.corporate_proxy) {
        $argList += @('--fallback-proxy', "$($cfg.corporate_proxy)")
    }
    $bypassVia = 'direct'
    if ($cfg.PSObject.Properties.Name -contains 'proxy_bypass_via' -and $cfg.proxy_bypass_via) {
        $bypassVia = "$($cfg.proxy_bypass_via)".Trim().ToLowerInvariant()
    }
    if ($bypassVia -notin @('direct', 'corporate')) { $bypassVia = 'direct' }
    $argList += @('--bypass-via', $bypassVia)

    $seen = @{}
    foreach ($h in @($cfg.proxy_bypass)) {
        if (-not $h) { continue }
        $k = "$h".ToLowerInvariant()
        if ($seen.ContainsKey($k)) { continue }
        $seen[$k] = $true
        $argList += @('--bypass-host', "$h")
    }
    if ($mode -eq 'github') {
        $extra = @(
            'github.com', '*.github.com', '*.githubusercontent.com',
            '*.githubassets.com', '*.github.io', 'ghcr.io', '*.ghcr.io'
        )
        foreach ($h in @($cfg.blocked_hosts) + $extra) {
            if (-not $h) { continue }
            $k = "$h".ToLowerInvariant()
            if ($seen.ContainsKey("t:$k")) { continue }
            $seen["t:$k"] = $true
            $argList += @('--pac-host', "$h")
        }
    }
    return $argList
}

function Start-HttpBridge([object]$cfg) {
    $httpPort = Get-HttpBridgePort
    # Always restart so PAC / bypass from config.json are applied
    Stop-HttpBridge
    $py = Get-PythonCommand
    $argList = @()
    if ($py.Prefix.Count -gt 0) { $argList += $py.Prefix }
    $argList += Get-BridgeArgList $cfg
    $bypassN = @($cfg.proxy_bypass).Count
    Write-Info "HTTP bridge 127.0.0.1:$httpPort scope=$(Get-SocksScope) bypass=$bypassN"
    if ($IsWindowsOs) {
        $proc = Start-Process -FilePath $py.Exe -ArgumentList $argList -PassThru -WindowStyle Hidden
    }
    else {
        $proc = Start-Process -FilePath $py.Exe -ArgumentList $argList -PassThru
    }
    Set-Content -Path $BridgePidPath -Value $proc.Id
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 150
        if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
            Remove-Item $BridgePidPath -ErrorAction SilentlyContinue
            throw 'http-via-socks bridge exited immediately'
        }
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', $httpPort)
            $ok = $c.Connected
            $c.Close()
            if ($ok) { return $httpPort }
        } catch {}
    }
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $BridgePidPath -ErrorAction SilentlyContinue
    throw "HTTP bridge port $httpPort never opened"
}

function Notify-WinProxyChange {
    if (-not $IsWindowsOs) { return }
    try {
        Add-Type -ErrorAction SilentlyContinue -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class OpsContentWinInet {
  [DllImport("wininet.dll", SetLastError=true)]
  public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
}
"@
        [void][OpsContentWinInet]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0) # SETTINGS_CHANGED
        [void][OpsContentWinInet]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0) # REFRESH
    } catch {}
}

function Enable-BrowserProxy([object]$cfg) {
    if (-not $IsWindowsOs) {
        Write-Warn 'Linux browser proxy: use ./ops-content.sh on (gsettings / var/cli.env)'
        return
    }
    $httpPort = Get-HttpBridgePort
    $scope = Get-SocksScope
    $pacUrl = "http://127.0.0.1:$httpPort/proxy.pac"
    Backup-WinProxy
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    # PAC for both scopes: full = all except proxy_bypass; github = only blocked_hosts
    Set-ItemProperty -Path $key -Name ProxyEnable -Value 0
    Set-ItemProperty -Path $key -Name AutoDetect -Value 0
    Set-ItemProperty -Path $key -Name AutoConfigURL -Value $pacUrl
    Notify-WinProxyChange
    $n = @($cfg.proxy_bypass).Count
    if ($scope -eq 'full') {
        Write-Ok "Browser PAC FULL = $pacUrl (VPS except proxy_bypass=$n hosts)"
    }
    else {
        Write-Ok "Browser PAC = $pacUrl (GitHub via VPS, else Squid; bypass=$n)"
    }
    Write-Info 'Restart Edge/Chrome tabs if sites still look cached/blocked'
    Write-Info 'Bypass list: config.json -> proxy_bypass (e.g. "*.tu-bryansk.ru")'
}

function Disable-BrowserProxy {
    if (-not $IsWindowsOs) { return }
    Restore-WinProxy
}

function Set-GitSocks([object]$cfg) {
    # GCM (.NET) rejects socks5h:// — use local HTTP CONNECT bridge instead.
    $httpPort = Start-HttpBridge $cfg
    $proxy = "http://127.0.0.1:$httpPort"
    Clear-InsteadOf
    git config --global http.proxy $proxy
    git config --global https.proxy $proxy
    git config --global credential.https://github.com.provider generic 2>$null
    Write-Ok "git http(s).proxy = $proxy (via SOCKS $($cfg.ssh.local_socks_port), scope=$(Get-SocksScope))"
    Enable-BrowserProxy $cfg
}

function Clear-GitProxy {
    git config --global --unset http.proxy 2>$null
    git config --global --unset https.proxy 2>$null
}

function Clear-InsteadOf {
    $lines = git config --global --get-regexp 'url\..*\.insteadof' 2>$null
    if (-not $lines) { return }
    foreach ($line in $lines) {
        if ($line -match 'ops-content|proxy-kill|/https/github') {
            $key = ($line -split '\s+', 2)[0]
            git config --global --unset-all $key 2>$null
        }
    }
}

function Start-Tunnel {
    $cfg = Get-Config
    if ($cfg.ssh.host -match 'YOUR_VPS') {
        throw 'Set real ssh.host in config.json (and preferably ssh.port=443)'
    }

    Write-Info "Probing CONNECT $($cfg.ssh.host):$($cfg.ssh.port) via Squid..."
    $probeExit = Invoke-Python -ArgList @($ProbePy, [string]$cfg.ssh.host, [string]$cfg.ssh.port)
    if ($probeExit -ne 0) {
        throw @"
CONNECT to $($cfg.ssh.host):$($cfg.ssh.port) failed (403=ACL, 503=nothing listening / firewall).
Office Squid allows *:443 but not :22. On the VPS (provider console), run:
  bash modes/vps/bootstrap_sshd_443.sh
Then: .\ops-content.ps1 probe $($cfg.ssh.host) 443
"@
    }

    if (Test-Path $PidPath) {
        $oldPid = [int](Get-Content $PidPath)
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
            Write-Warn "Tunnel already running (pid=$oldPid)"
            Set-GitSocks $cfg
            return
        }
    }

    $env:OPS_CONTENT_HTTP_PROXY = $cfg.corporate_proxy
    $sshArgs = Get-SshArgs $cfg
    Write-Info "SSH SOCKS -> 127.0.0.1:$($cfg.ssh.local_socks_port) via Squid"
    Write-Info "ProxyCommand: $(Get-ProxyCommand $cfg)"
    $proc = Start-SshProcess $sshArgs
    Set-Content -Path $PidPath -Value $proc.Id

    $port = [int]$cfg.ssh.local_socks_port
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
            Remove-Item $PidPath -ErrorAction SilentlyContinue
            throw 'ssh exited immediately; check user/key/sshd'
        }
        $listening = $false
        if ($IsWindowsOs -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
            $listening = [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        }
        else {
            try {
                $client = New-Object System.Net.Sockets.TcpClient
                $client.Connect('127.0.0.1', $port)
                $listening = $client.Connected
                $client.Close()
            } catch { $listening = $false }
        }
        if ($listening) {
            $ok = $true
            break
        }
    }
    if (-not $ok) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Remove-Item $PidPath -ErrorAction SilentlyContinue
        throw @"
SSH SOCKS port $port never opened. Check: key authorized on VPS, sshd on :443, user=$($cfg.ssh.user).
Manual: ssh -vvv -p 443 -i $($cfg.ssh.identity_file) $($cfg.ssh.user)@$($cfg.ssh.host)
"@
    }

    Set-GitSocks $cfg
    $httpPort = Get-HttpBridgePort
    @{ mode = 'ssh'; scope = (Get-SocksScope); pid = $proc.Id; socks = "socks5h://127.0.0.1:$port"; http = "http://127.0.0.1:$httpPort"; started = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content $StatePath
    Write-Ok "SSH tunnel ready scope=$(Get-SocksScope) (traffic via your VPS)"
}

function Stop-Tunnel {
    Disable-BrowserProxy
    Stop-HttpBridge
    if (Test-Path $PidPath) {
        $oldPid = [int](Get-Content $PidPath)
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
            Stop-Process -Id $oldPid -Force
            Write-Ok "ssh pid=$oldPid stopped"
        }
        Remove-Item $PidPath -ErrorAction SilentlyContinue
    }
    Clear-GitProxy
    Clear-InsteadOf
    Remove-Item $StatePath -ErrorAction SilentlyContinue
    Write-Ok 'git proxy cleared'
}

function Show-Status {
    $mode = Get-Mode
    Write-Host "MODE (.env)       = $mode"
    if ($mode -eq 'socks') {
        Write-Host "SOCKS_SCOPE       = $(Get-SocksScope)"
    }
    Write-Host "git http.proxy  = $(git config --global --get http.proxy 2>$null)"
    Write-Host "git https.proxy = $(git config --global --get https.proxy 2>$null)"
    $instead = git config --global --get-regexp 'url\..*\.insteadof' 2>$null
    if ($instead) {
        Write-Host 'git insteadOf:'
        $instead | ForEach-Object { Write-Host "  $_" }
    }
    if (Test-Path $PidPath) {
        $oldPid = [int](Get-Content $PidPath)
        if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
            Write-Ok "ssh tunnel pid=$oldPid running"
        } else {
            Write-Warn "pid=$oldPid dead"
        }
    }
    if (Test-Path $BridgePidPath) {
        $bPid = [int](Get-Content $BridgePidPath)
        if (Get-Process -Id $bPid -ErrorAction SilentlyContinue) {
            Write-Ok "http-bridge pid=$bPid running (port=$(Get-HttpBridgePort))"
        } else {
            Write-Warn "bridge pid=$bPid dead"
        }
    }
    if (Test-Path $StatePath) {
        Write-Host "state: $(Get-Content -Raw $StatePath)"
    }
    if (Test-Path $ConfigPath) {
        $cfg = Get-Config
        Write-Host "corporate_proxy = $($cfg.corporate_proxy)"
        Write-Host "ssh             = $($cfg.ssh.user)@$($cfg.ssh.host):$($cfg.ssh.port)"
        if ($cfg.worker_base_url) {
            Write-Host "worker_base_url = $($cfg.worker_base_url)"
        }
        $bn = @($cfg.proxy_bypass).Count
        Write-Host "proxy_bypass    = $bn entries"
    }
}

function Get-CurlCommand {
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) { return 'curl.exe' }
    if (Get-Command curl -ErrorAction SilentlyContinue) { return 'curl' }
    throw 'curl not found'
}

function Test-Bypass {
    $cfg = Get-Config
    $curl = Get-CurlCommand
    $nullOut = if ($IsWindowsOs) { 'NUL' } else { '/dev/null' }
    Write-Info '1) GitHub via Squid (expect 403)'
    & $curl -sS -o $nullOut -w "   HTTP %{http_code}`n" -x "http://$($cfg.corporate_proxy)" -m 10 -I https://github.com 2>$null
    Write-Info '2) git ls-remote'
    $env:GIT_TERMINAL_PROMPT = '0'
    git ls-remote https://github.com/git/git HEAD 2>&1 | Select-Object -First 5
}

function Invoke-Probe {
    if (-not $ProbeHost) {
        throw 'Usage: .\ops-content.ps1 probe HOST [PORT]'
    }
    $code = Invoke-Python -ArgList @($ProbePy, $ProbeHost, "$ProbePort")
    if ($code -ne 0) {
        throw "probe failed (exit=$code)"
    }
}

function Enable-Relay {
    # Optional: HTTPS git relay on YOUR VPS (modes/vps/github_proxy.py)
    $cfg = Get-Config
    if (-not $cfg.worker_base_url) {
        throw @"
Set worker_base_url in config.json to your VPS HTTPS relay.

  modes/vps/github_proxy.py + Caddy → worker_base_url
  Or use MODE=socks: .\ops-content.ps1 on
"@
    }
    $base = $cfg.worker_base_url.TrimEnd('/')
    if ($base -match 'ghfast\.top|ghproxy|kkgithub|netlify\.app|deno\.dev|pages\.dev|workers\.dev') {
        throw 'worker_base_url must be YOUR VPS, not a public SaaS mirror.'
    }

    Clear-GitProxy
    Clear-InsteadOf

    $hosts = @(
        @{ from = 'https://github.com/'; to = "$base/https/github.com/" },
        @{ from = 'https://api.github.com/'; to = "$base/https/api.github.com/" },
        @{ from = 'https://codeload.github.com/'; to = "$base/https/codeload.github.com/" },
        @{ from = 'https://raw.githubusercontent.com/'; to = "$base/https/raw.githubusercontent.com/" },
        @{ from = 'https://objects.githubusercontent.com/'; to = "$base/https/objects.githubusercontent.com/" }
    )
    foreach ($h in $hosts) {
        git config --global "url.$($h.to).insteadOf" $h.from
    }
    git config --global http.proxy "http://$($cfg.corporate_proxy)"
    git config --global https.proxy "http://$($cfg.corporate_proxy)"
    git config --global --unset-all http.extraHeader 2>$null
    git config --global http.extraHeader 'Accept-Encoding: identity'
    if ($env:OPS_CONTENT_SECRET) {
        git config --global "http.$base/.extraHeader" "X-Ops-Content-Token: $($env:OPS_CONTENT_SECRET)"
    }
    else {
        Write-Warn 'OPS_CONTENT_SECRET empty — relay auth disabled on client; set the same secret on the VPS'
    }
    git config --global http.version HTTP/1.1
    git config --global protocol.version 1
    git config --global http.postBuffer 524288000

    @{ mode = 'relay'; base = $base; started = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content $StatePath
    Write-Ok "Relay ON -> $base (your VPS)"
    Write-Info 'Check: git ls-remote https://github.com/git/git HEAD'
}

function Disable-Relay {
    $cfg = $null
    try { $cfg = Get-Config } catch { }
    $base = if ($cfg -and $cfg.worker_base_url) { $cfg.worker_base_url.TrimEnd('/') } else { '' }
    Clear-InsteadOf
    Clear-GitProxy
    git config --global --unset-all http.extraHeader 2>$null
    if ($base) {
        git config --global --unset-all "http.$base/.extraHeader" 2>$null
    }
    git config --global --unset http.version 2>$null
    git config --global --unset protocol.version 2>$null
    git config --global --unset http.postBuffer 2>$null
    Remove-Item $StatePath -ErrorAction SilentlyContinue
    Write-Ok 'Relay OFF'
}

function Backup-WinProxy {
    if (Test-Path $ProxyBackupPath) { return }
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    $p = Get-ItemProperty -Path $key
    @{
        ProxyEnable = [int]($p.ProxyEnable)
        ProxyServer = [string]($p.ProxyServer)
        ProxyOverride = [string]($p.ProxyOverride)
        AutoConfigURL = [string]($p.AutoConfigURL)
        AutoDetect = $(if ($null -ne $p.AutoDetect) { [int]$p.AutoDetect } else { 0 })
    } | ConvertTo-Json | Set-Content -Path $ProxyBackupPath -Encoding UTF8
}

function Restore-WinProxy {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    if (Test-Path $ProxyBackupPath) {
        $b = Get-Content -Raw -Encoding UTF8 $ProxyBackupPath | ConvertFrom-Json
        Set-ItemProperty -Path $key -Name ProxyEnable -Value ([int]$b.ProxyEnable)
        if ($null -ne $b.ProxyServer) {
            Set-ItemProperty -Path $key -Name ProxyServer -Value ([string]$b.ProxyServer)
        }
        if ($null -ne $b.ProxyOverride) {
            Set-ItemProperty -Path $key -Name ProxyOverride -Value ([string]$b.ProxyOverride)
        }
        if ($b.PSObject.Properties.Name -contains 'AutoDetect') {
            Set-ItemProperty -Path $key -Name AutoDetect -Value ([int]$b.AutoDetect)
        }
        if ($b.AutoConfigURL) {
            Set-ItemProperty -Path $key -Name AutoConfigURL -Value ([string]$b.AutoConfigURL)
        }
        else {
            Remove-ItemProperty -Path $key -Name AutoConfigURL -ErrorAction SilentlyContinue
        }
        Remove-Item $ProxyBackupPath -ErrorAction SilentlyContinue
        Notify-WinProxyChange
        Write-Ok 'Windows proxy restored from backup'
    }
    else {
        Remove-ItemProperty -Path $key -Name AutoConfigURL -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $key -Name ProxyEnable -Value 0
        Notify-WinProxyChange
        Write-Ok 'Windows proxy disabled'
    }
}

function Enable-ByMode {
    $mode = Get-Mode
    Write-Info "MODE=$mode"
    switch ($mode) {
        'socks' { Start-Tunnel }
        'vps' {
            $cfg = Get-Config
            if ($cfg.worker_base_url -and $cfg.worker_base_url -notmatch 'YOUR_|ghfast|netlify|deno\.dev|pages\.dev|workers\.dev') {
                Enable-Relay
            }
            else {
                Start-Tunnel
            }
        }
        default {
            throw "Unknown MODE=$mode (use socks|vps). See config/.env.example"
        }
    }
}

function Disable-ByMode {
    $mode = Get-Mode
    Write-Info "MODE=$mode -> off"
    switch ($mode) {
        'socks' { Stop-Tunnel }
        'vps' {
            Stop-Tunnel
            Disable-Relay
        }
        default {
            Stop-Tunnel
            Disable-Relay
        }
    }
}

function Invoke-Deploy {
    if (-not (Test-Path $DeployPs1)) {
        throw "Missing deploy.ps1"
    }
    & $DeployPs1 deploy
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        exit $LASTEXITCODE
    }
}

function Show-Help {
    Write-Host 'ops-content - SOCKS/VPS tunnel through corporate Squid'
    Write-Host ''
    Write-Host 'Config: config.json + .env  (examples: config/)'
    Write-Host ''
    Write-Host 'MODE=socks (default):'
    Write-Host '  SOCKS_SCOPE=full|github   proxy_bypass in config.json'
    Write-Host '  on / off                  tunnel + browser PAC'
    Write-Host '  install-service           Linux systemd --user'
    Write-Host ''
    Write-Host 'MODE=vps: HTTPS git relay on your server (worker_base_url)'
    Write-Host 'Other: start/stop probe status test deploy relay-on/off help'
}

switch ($Command) {
    'init' { Invoke-Init }
    'start' { Start-Tunnel }
    'stop' { Stop-Tunnel }
    'status' { Show-Status }
    'test' { Test-Bypass }
    'probe' { Invoke-Probe }
    'on' { Enable-ByMode }
    'off' { Disable-ByMode }
    'deploy' { Invoke-Deploy }
    'relay-on' { Enable-Relay }
    'relay-off' { Disable-Relay }
    'install-service' {
        Write-Warn 'systemd service is Linux-only. On Windows use: .\ops-content.ps1 on'
        Write-Info 'Linux: ./ops-content.sh install-service'
    }
    'uninstall-service' {
        Write-Warn 'systemd service is Linux-only: ./ops-content.sh uninstall-service'
    }
    default { Show-Help }
}
