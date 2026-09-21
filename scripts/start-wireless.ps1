[CmdletBinding()]
param(
    [ValidateSet("pc", "pi")]
    [string]$Mode = "pc",
    [ValidateRange(1, 65535)]
    [int]$Port = 5056,
    [ValidateRange(1, 65535)]
    [int]$DashboardPort = $(if ($env:BASS_DASHBOARD_PORT) { [int]$env:BASS_DASHBOARD_PORT } else { 5057 }),
    [string[]]$AdvertiseAddress = @(),
    [switch]$HardwareSimulator
)

$ErrorActionPreference = "Stop"
if ($HardwareSimulator -and $Mode -ne "pc") {
    throw "Hardware simulation requires PC development mode."
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $repoRoot ".venv-wireless"
$wirelessPython = Join-Path $venvDir "Scripts\python.exe"
$requirements = Join-Path $repoRoot "requirements-wireless.txt"
$entrypoint = Join-Path $repoRoot "bass_wireless.py"

if (-not (Test-Path -LiteralPath $wirelessPython)) {
    $bootstrapPython = (Get-Command python -ErrorAction Stop).Source
    Write-Host "Creating isolated wireless Python environment..."
    & $bootstrapPython -m venv --system-site-packages $venvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create $venvDir"
    }
}

$httpRequirements = Join-Path $repoRoot "requirements-http.txt"
$dependencyProbe = @'
import sys
from pathlib import Path
from importlib.metadata import version
import flask, cv2, insightface, onnxruntime, qrcode, zeroconf, cheroot, OpenSSL

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    requirement = line.strip()
    if not requirement or requirement.startswith("#"):
        continue
    package, expected = requirement.split("==", 1)
    if version(package) != expected:
        raise RuntimeError(f"{package} requires {expected}")
'@
$dependencyProbe | & $wirelessPython - $httpRequirements 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing missing wireless host dependencies..."
    & $wirelessPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Wireless dependency installation failed."
    }
}

$firewallRules = Get-NetFirewallRule -Group "BASS Wireless" -ErrorAction SilentlyContinue
if (-not $firewallRules) {
    $firewallScript = Join-Path $PSScriptRoot "setup-wireless-firewall.ps1"
    Write-Warning "Private-network firewall rules are not installed."
    Write-Host "Run PowerShell as Administrator once:"
    Write-Host "  & '$firewallScript' -Port $Port"
}

$launcherArguments = @(
    $entrypoint,
    "--mode", $Mode,
    "--host", "0.0.0.0",
    "--port", $Port,
    "--dashboard-port", $DashboardPort
)
foreach ($address in $AdvertiseAddress) {
    $launcherArguments += @("--advertise-address", $address)
}
if ($HardwareSimulator) {
    $launcherArguments += "--hardware-simulator"
}

Push-Location $repoRoot
try {
    & $wirelessPython @launcherArguments
    $serverExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($serverExitCode -ne 0) {
    throw "BASS wireless host exited with code $serverExitCode."
}
