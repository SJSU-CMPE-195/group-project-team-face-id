[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5058
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$clientPython = Join-Path $repoRoot ".venv-wireless\Scripts\python.exe"
$entrypoint = Join-Path $repoRoot "bass_pc_client.py"

if (-not (Test-Path -LiteralPath $clientPython)) {
    throw "Missing .venv-wireless Python environment. Follow docs/pc-bluetooth.md."
}
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "dist\index.html"))) {
    throw "Build the dashboard first with npm run build."
}

& $clientPython -c "import flask, cheroot, cryptography"
if ($LASTEXITCODE -ne 0) {
    throw "PC client HTTP dependencies are missing. Follow docs/pc-bluetooth.md."
}

Push-Location $repoRoot
try {
    & $clientPython $entrypoint --port $Port
    $clientExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($clientExitCode -ne 0) {
    throw "BASS PC Bluetooth client exited with code $clientExitCode."
}
