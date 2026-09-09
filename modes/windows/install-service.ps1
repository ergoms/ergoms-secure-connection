#Requires -Version 5.1
<#
.SYNOPSIS
  Install ERGOMS VPN as a Windows service (autostart, LocalSystem, TUN without UAC).
#>
param(
    [string]$PythonExe = '',
    [string]$Root = ''
)

$ErrorActionPreference = 'Stop'
$ServiceId = 'ergoms-vpn'
$LegacyServiceId = 'ops-content'
$WinswVersion = '2.12.0'
$WinswUrl = "https://github.com/winsw/winsw/releases/download/v$WinswVersion/WinSW-x64.exe"

if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = [Security.Principal.WindowsPrincipal]$id
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PythonExe {
    if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
        return $PythonExe
    }
    if ($env:ERGOMS_VPN_PYTHON -and (Test-Path -LiteralPath $env:ERGOMS_VPN_PYTHON)) {
        return $env:ERGOMS_VPN_PYTHON
    }
    if ($env:OPS_CONTENT_PYTHON -and (Test-Path -LiteralPath $env:OPS_CONTENT_PYTHON)) {
        return $env:OPS_CONTENT_PYTHON
    }
    $venvPy = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        return $venvPy
    }
    foreach ($name in @('python', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cmd.Source -match 'WindowsApps') { continue }
        if ($name -eq 'py') {
            & $cmd.Source -3 -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $out = & $cmd.Source -3 -c "import sys; print(sys.executable)"
                if ($out) { return $out.Trim() }
            }
        }
        else {
            & $cmd.Source -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $cmd.Source }
        }
    }
    throw 'Python 3 not found'
}

function Escape-XmlValue([string]$s) {
    return ((($s -replace '&', '&amp;') -replace '<', '&lt;') -replace '>', '&gt;') -replace '"', '&quot;'
}

if (-not (Test-Admin)) {
    $py = ''
    try { $py = Get-PythonExe } catch { $py = '' }
    $argList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-Root', $Root
    )
    if ($py) { $argList += @('-PythonExe', $py) }
    try {
        $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -Verb RunAs -Wait -PassThru -ArgumentList $argList
        exit $(if ($p) { $p.ExitCode } else { 1 })
    }
    catch {
        Write-Error "Administrator rights required: $_"
        exit 1
    }
}

Set-Location $Root

$config = Join-Path $Root 'config.json'
if (-not (Test-Path -LiteralPath $config)) {
    Write-Error "Missing $config - run: .\ergoms-vpn.ps1 init"
    exit 1
}

$py = Get-PythonExe
$tools = Join-Path $Root 'tools'
$logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $tools, $logs | Out-Null

$winsw = Join-Path $tools 'ergoms-vpn-service.exe'
$xmlPath = Join-Path $tools 'ergoms-vpn-service.xml'

if (-not (Test-Path -LiteralPath $winsw)) {
    Write-Host "[ERGOMS VPN] downloading WinSW $WinswVersion -> tools/ergoms-vpn-service.exe"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $tmp = Join-Path $tools 'ergoms-vpn-service.exe.download'
    try {
        Invoke-WebRequest -Uri $WinswUrl -OutFile $tmp -UseBasicParsing
        Move-Item -LiteralPath $tmp -Destination $winsw -Force
    }
    catch {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        Write-Error @"
Failed to download WinSW ($WinswUrl): $_
Download WinSW-x64.exe manually and save as:
  $winsw
Then retry: .\ergoms-vpn.ps1 install-service
"@
        exit 1
    }
}

# Avoid two clients fighting for :1080 / TUN
$env:PYTHONPATH = $Root
& $py -m desktop off 2>$null | Out-Null

foreach ($id in @($ServiceId, $LegacyServiceId)) {
    $existing = Get-Service -Name $id -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "[ERGOMS VPN] replacing existing service $id"
        & $winsw stop 2>$null | Out-Null
        Start-Sleep -Seconds 1
        & $winsw uninstall 2>$null | Out-Null
        sc.exe delete $id 2>$null | Out-Null
        Start-Sleep -Seconds 1
    }
}

$pyEsc = Escape-XmlValue $py
$rootEsc = Escape-XmlValue $Root
$logsEsc = Escape-XmlValue $logs

$xml = @"
<service>
  <id>$ServiceId</id>
  <name>ERGOMS VPN</name>
  <description>ERGOMS VPN VLESS+Reality (sing-box TUN + SOCKS/HTTP)</description>
  <executable>$pyEsc</executable>
  <arguments>-u -m desktop watch</arguments>
  <workingdirectory>$rootEsc</workingdirectory>
  <stopexecutable>$pyEsc</stopexecutable>
  <stoparguments>-u -m desktop off</stoparguments>
  <stoptimeout>20 sec</stoptimeout>
  <logpath>$logsEsc</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
  <onfailure action="restart" delay="5 sec"/>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="none"/>
  <resetfailure>1 hour</resetfailure>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <depend>Tcpip</depend>
  <env name="PYTHONUNBUFFERED" value="1"/>
  <env name="PYTHONPATH" value="$rootEsc"/>
  <env name="ERGOMS_VPN_DATA" value="$rootEsc"/>
</service>
"@
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($xmlPath, $xml.TrimStart() + "`n", $utf8)

$installOut = & $winsw install 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -and $installOut -notmatch 'already exists') {
    Write-Error "WinSW install failed:`n$installOut"
    exit 1
}

& $winsw start 2>&1 | Out-Null
Start-Sleep -Seconds 2

$svc = Get-Service -Name $ServiceId -ErrorAction SilentlyContinue
Write-Host "Installed: service $ServiceId ($winsw)"
Write-Host "Start:     Start-Service $ServiceId"
Write-Host "Status:    Get-Service $ServiceId"
Write-Host "Logs:      $logs\ergoms-vpn-service.wrapper.log  and  logs\watchdog.log"
Write-Host "Remove:    .\ergoms-vpn.ps1 uninstall-service"
Write-Host ""
if ($svc) {
    Write-Host ("State:     {0}" -f $svc.Status)
    if ($svc.Status -ne 'Running') {
        Write-Host "Service is not Running yet - see logs\ergoms-vpn-service*.log" -ForegroundColor Yellow
    }
}
else {
    Write-Host "Get-Service $ServiceId did not find the service - see WinSW output" -ForegroundColor Yellow
}
