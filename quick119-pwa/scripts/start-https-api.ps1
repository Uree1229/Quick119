param(
    [string]$CertFile = (Join-Path $PSScriptRoot "../certs/quick119.pem"),
    [string]$KeyFile = (Join-Path $PSScriptRoot "../certs/quick119-key.pem"),
    [int]$Port = 8443,
    [string]$BindAddress = "0.0.0.0",
    [switch]$CheckKakaoApi
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (!(Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Install requirements in .venv first." }
$certPath = (Resolve-Path -LiteralPath $CertFile).Path
$keyPath = (Resolve-Path -LiteralPath $KeyFile).Path
if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid port." }
# Keep the development database; load credentials only inside the application.
$env:QUICK119_ENV = "development"
$env:QUICK119_DB_PATH = Join-Path $projectRoot "data/quick119-dev.db"
$env:QUICK119_EXTERNAL_API = "1"
$env:QUICK119_LOAD_ENV = "1"
Write-Host "QUICK119 HTTPS development server with external APIs enabled. Credentials are not logged."
Push-Location $projectRoot
try {
    # Uses exactly the server's startup loader. Prints presence/status only, never credentials.
    if ($CheckKakaoApi) {
        & $pythonPath (Join-Path $PSScriptRoot "check-kakao-api.py") --live
        if ($LASTEXITCODE -ne 0) { throw "Kakao Mobility check failed. See sanitized status above." }
        return
    }
    & $pythonPath -m uvicorn main:app --host $BindAddress --port $Port --ssl-certfile $certPath --ssl-keyfile $keyPath --no-access-log
    if ($LASTEXITCODE -ne 0) { throw "HTTPS server exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
