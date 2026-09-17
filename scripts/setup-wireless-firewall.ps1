[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5056
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdministrator) {
    throw "Run this script from an elevated PowerShell window (Run as Administrator)."
}

Get-NetFirewallRule -Group "BASS Wireless" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

New-NetFirewallRule `
    -DisplayName "BASS Wireless API $Port (Private)" `
    -Group "BASS Wireless" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Private `
    -RemoteAddress LocalSubnet | Out-Null

New-NetFirewallRule `
    -DisplayName "BASS mDNS 5353 (Private)" `
    -Group "BASS Wireless" `
    -Direction Inbound `
    -Action Allow `
    -Protocol UDP `
    -LocalPort 5353 `
    -Profile Private `
    -RemoteAddress LocalSubnet | Out-Null

Write-Host "BASS firewall rules installed for Private networks only."
Write-Host "  Device API TCP: $Port"
Write-Host "  mDNS UDP:       5353"

