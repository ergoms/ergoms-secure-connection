#Requires -Version 5.1
# Legacy alias → ergoms-vpn.ps1
& (Join-Path $PSScriptRoot 'ergoms-vpn.ps1') @args
exit $LASTEXITCODE
