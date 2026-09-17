[CmdletBinding()]
param(
    [ValidateSet("pc", "pi")]
    [string]$Mode = "pc",
    [ValidateRange(1, 65535)]
    [int]$Port = 5056,
    [string[]]$AdvertiseAddress = @()
)

$ErrorActionPreference = "Stop"
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

& $wirelessPython -c "import flask, cv2, insightface, onnxruntime, qrcode, zeroconf" 2>$null
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
    "--port", $Port
)
foreach ($address in $AdvertiseAddress) {
    $launcherArguments += @("--advertise-address", $address)
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
