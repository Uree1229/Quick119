param(
    [Parameter(Mandatory = $true)][string]$CertFile,
    [Parameter(Mandatory = $true)][string]$KeyFile,
    [int]$Port = 8443,
    [string]$BindAddress = "0.0.0.0"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (!(Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Install requirements in .venv first." }
$certPath = (Resolve-Path -LiteralPath $CertFile).Path
$keyPath = (Resolve-Path -LiteralPath $KeyFile).Path
if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid port." }
# Always isolate this local HTTPS run from production data and real API keys.
$env:QUICK119_ENV = "development"
$env:QUICK119_DB_PATH = Join-Path $projectRoot "data/quick119-dev.db"
$env:QUICK119_EXTERNAL_API = "0"
$env:QUICK119_LOAD_ENV = "0"
Write-Host "QUICK119 HTTPS development server. Use synthetic data on a trusted private network only."
Push-Location $projectRoot
try {
    & $pythonPath -m uvicorn main:app --host $BindAddress --port $Port --ssl-certfile $certPath --ssl-keyfile $keyPath --no-access-log
    if ($LASTEXITCODE -ne 0) { throw "HTTPS server exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
