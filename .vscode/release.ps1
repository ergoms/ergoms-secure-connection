#Requires -Version 5.1
param(
    [ValidateSet('all', 'windows', 'linux', 'publish')]
    [string]$Target = 'all',
    [string]$WslDistro = 'Ubuntu-24.04',
    [string]$GitHubRepo = 'ergoms/ergoms-secure-connection'
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

function Get-AppVersion {
    foreach ($line in Get-Content -LiteralPath (Join-Path $Root 'desktop\__init__.py')) {
        if ($line -match '__version__\s*=\s*"([^"]+)"') { return $Matches[1] }
    }
    throw 'could not read desktop/__init__.py __version__'
}

function Get-ArtifactsDir {
    $dir = Join-Path $Root 'artifacts'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    return $dir
}

function Invoke-WindowsRelease {
    $ver = Get-AppVersion
    $out = Get-ArtifactsDir
    Write-Host "Windows build $ver"
    & (Join-Path $Root '.vscode\setup.ps1') -Target build
    if ($LASTEXITCODE -ne 0) { throw "setup.ps1 build failed: $LASTEXITCODE" }

    $srcSetup = Join-Path $Root 'dist\ERGOMS SECURE CONNECTION-Setup.exe'
    $srcDir = Join-Path $Root 'dist\ERGOMS SECURE CONNECTION'
    if (-not (Test-Path -LiteralPath $srcSetup)) { throw "missing $srcSetup" }
    if (-not (Test-Path -LiteralPath $srcDir)) { throw "missing $srcDir" }

    $setup = Join-Path $out "ERGOMS-SECURE-CONNECTION-$ver-windows-x64-setup.exe"
    $zip = Join-Path $out "ERGOMS-SECURE-CONNECTION-$ver-windows-x64.zip"
    Copy-Item -LiteralPath $srcSetup -Destination $setup -Force
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    & tar.exe -a -c -f $zip -C (Join-Path $Root 'dist') 'ERGOMS SECURE CONNECTION'
    if ($LASTEXITCODE -ne 0) { throw "tar zip failed: $LASTEXITCODE" }
    Write-Host "OK: $setup"
    Write-Host "OK: $zip"
}

function Invoke-LinuxRelease {
    $ver = Get-AppVersion
    Get-ArtifactsDir | Out-Null
    if ($Root -notmatch '^([A-Za-z]):\\(.+)$') { throw "cannot map $Root to WSL" }
    $wslRoot = "/mnt/$($Matches[1].ToLower())/$($Matches[2] -replace '\\', '/')"
    $script = "$wslRoot/.vscode/release-linux.sh"
    Write-Host "Linux build $ver via WSL $WslDistro ($script)"
    & wsl.exe -d $WslDistro -- bash -lc "chmod +x '$script' && '$script' '$wslRoot'"
    if ($LASTEXITCODE -ne 0) { throw "WSL linux build failed: $LASTEXITCODE" }
    $tar = Join-Path (Get-ArtifactsDir) "ERGOMS-SECURE-CONNECTION-$ver-linux-x64.tar.gz"
    if (-not (Test-Path -LiteralPath $tar)) { throw "missing $tar" }
    Write-Host "OK: $tar"
}

function Get-ReleaseFiles {
    $ver = Get-AppVersion
    $out = Get-ArtifactsDir
    $wanted = @(
        "ERGOMS-SECURE-CONNECTION-$ver-windows-x64-setup.exe"
        "ERGOMS-SECURE-CONNECTION-$ver-windows-x64.zip"
        "ERGOMS-SECURE-CONNECTION-$ver-linux-x64.tar.gz"
    )
    $files = @()
    foreach ($name in $wanted) {
        $path = Join-Path $out $name
        if (Test-Path -LiteralPath $path) { $files += $path }
    }
    if (-not $files) { throw "no artifacts in $out - run windows/linux first" }
    return $files
}

function Invoke-Publish {
    $ver = Get-AppVersion
    $tag = "v$ver"
    $files = Get-ReleaseFiles
    Write-Host "GitHub Release $tag -> $GitHubRepo"
    $files | ForEach-Object { Write-Host "  $_" }

    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh) {
        Write-Host "gh not found. Upload manually:"
        Write-Host "  https://github.com/$GitHubRepo/releases/new?tag=$tag"
        return
    }

    $ghArgs = @('release', 'create', $tag, '--repo', $GitHubRepo, '--title', "ERGOMS SECURE CONNECTION $ver", '--generate-notes')
    $ghArgs += $files
    & $gh.Source @ghArgs
    if ($LASTEXITCODE -eq 0) { return }

    Write-Host "create failed - trying upload into existing $tag"
    & $gh.Source release upload $tag --repo $GitHubRepo --clobber @files
    if ($LASTEXITCODE -ne 0) {
        Write-Host "gh cannot reach $GitHubRepo. Upload in the browser:"
        Write-Host "  https://github.com/$GitHubRepo/releases/new?tag=$tag"
        Write-Host "Files:"
        $files | ForEach-Object { Write-Host "  $_" }
        throw "GitHub Release publish failed"
    }
}

switch ($Target) {
    'windows' { Invoke-WindowsRelease }
    'linux' { Invoke-LinuxRelease }
    'publish' { Invoke-Publish }
    'all' { Invoke-WindowsRelease; Invoke-LinuxRelease; Invoke-Publish }
}
