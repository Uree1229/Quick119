param([int]$Port = 18033, [switch]$RefreshSyntheticResources)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Choose a valid port.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$localPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$teamPython = Join-Path $projectRoot '.local/team-venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $teamPython)) {
    & $localPython -m venv --without-pip (Join-Path $projectRoot '.local/team-venv')
    if ($LASTEXITCODE -ne 0) { throw 'Isolated team Python setup failed.' }
}
# Create a clean child environment: do not inherit API keys or proxy credentials.
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $teamPython
$startInfo.WorkingDirectory = $projectRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.Arguments = '"' + (Join-Path $PSScriptRoot 'run-team-chat.py') + '" --port ' + $Port
if ($RefreshSyntheticResources) { $startInfo.Arguments += ' --refresh-synthetic-resources' }
$startInfo.EnvironmentVariables.Clear()
foreach ($name in @('SystemRoot','WINDIR','TEMP','TMP','PATH')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($null -ne $value) { $startInfo.EnvironmentVariables[$name] = $value }
}
$startInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
$startInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
$process = [System.Diagnostics.Process]::Start($startInfo)
Write-Host "Team synthetic server: http://127.0.0.1:$Port/api/team-runtime (Ctrl+C to stop)"
try {
    while (!$process.WaitForExit(500)) { }
    if ($process.ExitCode -ne 0) { throw 'Team server exited with an error.' }
}
finally { if (!$process.HasExited) { $process.Kill(); $process.WaitForExit() }; $process.Dispose() }
