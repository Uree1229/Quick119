param([int]$Port = 18034, [int]$TeamPort = 18033, [switch]$SyntheticRoutes, [switch]$EnableCloudSpeech,
      [string]$TailscaleOrigin)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($Port -lt 1 -or $Port -gt 65535 -or $TeamPort -lt 1 -or $TeamPort -gt 65535 -or $Port -eq $TeamPort) {
    throw 'Choose two different valid ports.'
}
if ($PSBoundParameters.ContainsKey('TailscaleOrigin')) {
    if (!$SyntheticRoutes -or $EnableCloudSpeech) {
        throw '-TailscaleOrigin requires -SyntheticRoutes and cannot be used with -EnableCloudSpeech.'
    }
    if (!$TailscaleOrigin) { throw 'Pass the exact HTTPS URL from tailscale serve status.' }
}
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = Join-Path $projectRoot '.venv/Scripts/python.exe'
$startInfo.WorkingDirectory = $projectRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.Arguments = '-m uvicorn main:app --host 127.0.0.1 --port ' + $Port + ' --no-proxy-headers --no-access-log --lifespan on'
$startInfo.EnvironmentVariables.Clear()
foreach ($name in @('SystemRoot','WINDIR','TEMP','TMP','PATH')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($null -ne $value) { $startInfo.EnvironmentVariables[$name] = $value }
}
$startInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
$startInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
$startInfo.EnvironmentVariables['QUICK119_ENV'] = 'development'
$startInfo.EnvironmentVariables['QUICK119_LOAD_ENV'] = '0'
$startInfo.EnvironmentVariables['QUICK119_EXTERNAL_API'] = '0'
if ($EnableCloudSpeech) {
    $startInfo.EnvironmentVariables['QUICK119_EXTERNAL_API'] = '1'
    $startInfo.EnvironmentVariables['QUICK119_TTS_VOICE'] = 'neural'
}
$startInfo.EnvironmentVariables['QUICK119_DB_PATH'] = Join-Path $projectRoot '.local/team-chat/quick119-synthetic.db'
$startInfo.EnvironmentVariables['QUICK119_TEAM_CHAT_ENABLED'] = '1'
$startInfo.EnvironmentVariables['QUICK119_TEAM_CHAT_URL'] = 'http://127.0.0.1:' + $TeamPort
$startInfo.EnvironmentVariables['QUICK119_SYNTHETIC_ROUTES'] = $(if ($SyntheticRoutes) { '1' } else { '0' })
if ($TailscaleOrigin) {
    $startInfo.EnvironmentVariables['QUICK119_TAILSCALE_ORIGIN'] = $TailscaleOrigin
}
$process = [System.Diagnostics.Process]::Start($startInfo)
Write-Host "QUICK119 synthetic connection: http://127.0.0.1:$Port/api/team-runtime (Ctrl+C to stop)"
try {
    while (!$process.WaitForExit(500)) { }
    if ($process.ExitCode -ne 0) { throw 'FastAPI server exited with an error.' }
}
finally { if (!$process.HasExited) { $process.Kill(); $process.WaitForExit() }; $process.Dispose() }
