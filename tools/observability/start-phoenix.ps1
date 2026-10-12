# Optional local trace viewer. Does not enable tracing in the novel application.
$ErrorActionPreference = 'Stop'
$phoenixProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$phoenixExecutable = Join-Path $phoenixProjectRoot '.venv-phoenix/Scripts/phoenix.exe'
if (-not (Test-Path -LiteralPath $phoenixExecutable)) {
    throw 'Phoenix is not installed in .venv-phoenix. Create that environment and install tools/observability/requirements-phoenix.txt first.'
}
$phoenixSettings = @{
    PHOENIX_WORKING_DIR = (Join-Path $phoenixProjectRoot 'workspace/phoenix')
    PHOENIX_HOST = '127.0.0.1'
    PHOENIX_PORT = '6006'
    PHOENIX_TELEMETRY_ENABLED = 'false'
    PYTHONUTF8 = '1'
}
$phoenixPrevious = @{}
try {
    foreach ($phoenixKey in $phoenixSettings.Keys) {
        $phoenixPrevious[$phoenixKey] = [Environment]::GetEnvironmentVariable($phoenixKey, 'Process')
        [Environment]::SetEnvironmentVariable($phoenixKey, $phoenixSettings[$phoenixKey], 'Process')
    }
    Write-Host 'Phoenix: http://127.0.0.1:6006 (Ctrl+C to stop)'
    & $phoenixExecutable serve
} finally {
    foreach ($phoenixKey in $phoenixPrevious.Keys) {
        [Environment]::SetEnvironmentVariable($phoenixKey, $phoenixPrevious[$phoenixKey], 'Process')
    }
}
