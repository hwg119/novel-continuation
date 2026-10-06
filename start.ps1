[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendRoot = Join-Path $AppRoot "frontend"
$VenvRoot = Join-Path $AppRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$Url = "http://127.0.0.1:8765"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Stop-WithMessage([string]$Message) {
    Write-Host "`nStartup failed: $Message" -ForegroundColor Red
    exit 1
}

function Get-CommandVersion([string]$Command, [string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = (& $Command @Arguments 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0 -or $raw -notmatch '(\d+)\.(\d+)') { return $null }
    return [version]("{0}.{1}" -f $Matches[1], $Matches[2])
}

function Get-Sha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "")
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Test-PortOpen {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", 8765)
        return $task.Wait(350) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Open-Workbench {
    if (-not $NoBrowser) { Start-Process $Url }
}

Set-Location $AppRoot
Write-Host "Novel Continuation Workbench - Windows launcher" -ForegroundColor Green

$PythonCommand = $null
$PythonArguments = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidateVersion = Get-CommandVersion "py" @("-3", "--version")
    if ($null -ne $candidateVersion) {
        $PythonCommand = "py"
        $PythonArguments = @("-3")
    }
}
if (-not $PythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $candidateVersion = Get-CommandVersion "python" @("--version")
    if ($null -ne $candidateVersion) {
        $PythonCommand = "python"
    }
}
if (-not $PythonCommand -and (Test-Path $VenvPython)) {
    $candidateVersion = Get-CommandVersion $VenvPython @("--version")
    if ($null -ne $candidateVersion) {
        $PythonCommand = $VenvPython
    }
}
if (-not $PythonCommand) { Stop-WithMessage "Python 3 was not found. Install Python 3.10 or newer and enable Add Python to PATH." }
$PythonVersion = Get-CommandVersion $PythonCommand ($PythonArguments + @("--version"))
if ($null -eq $PythonVersion -or $PythonVersion -lt [version]"3.10") { Stop-WithMessage "Python 3.10 or newer is required." }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Stop-WithMessage "Node.js was not found. Install Node.js 20 or newer." }
$NodeVersion = Get-CommandVersion "node" @("--version")
if ($null -eq $NodeVersion -or $NodeVersion.Major -lt 20) { Stop-WithMessage "Node.js 20 or newer is required." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Stop-WithMessage "npm was not found. Reinstall Node.js." }

$RequirementsHash = Get-Sha256 (Join-Path $AppRoot "requirements.txt")
$RequirementsStamp = Join-Path $VenvRoot ".requirements.sha256"
$NeedsPythonInstall = -not (Test-Path $VenvPython)
if (-not $NeedsPythonInstall) {
    $SavedHash = if (Test-Path $RequirementsStamp) { (Get-Content $RequirementsStamp -Raw).Trim() } else { "" }
    $NeedsPythonInstall = $SavedHash -ne $RequirementsHash
}

$PackageLock = Join-Path $FrontendRoot "package-lock.json"
$FrontendHash = Get-Sha256 $PackageLock
$FrontendStamp = Join-Path $VenvRoot ".frontend-lock.sha256"
$SavedFrontendHash = if (Test-Path $FrontendStamp) { (Get-Content $FrontendStamp -Raw).Trim() } else { "" }
$NeedsNpmInstall = -not (Test-Path (Join-Path $FrontendRoot "node_modules")) -or $SavedFrontendHash -ne $FrontendHash

$DistIndex = Join-Path $FrontendRoot "dist\index.html"
$NeedsBuild = $NeedsNpmInstall -or -not (Test-Path $DistIndex)
if (-not $NeedsBuild) {
    $BuiltAt = (Get-Item $DistIndex).LastWriteTimeUtc
    $SourceFiles = Get-ChildItem (Join-Path $FrontendRoot "src") -Recurse -File
    $BuildInputs = $SourceFiles + @(
        (Get-Item (Join-Path $FrontendRoot "index.html")),
        (Get-Item (Join-Path $FrontendRoot "vite.config.ts")),
        (Get-Item (Join-Path $FrontendRoot "package.json")),
        (Get-Item $PackageLock)
    )
    $NeedsBuild = $null -ne ($BuildInputs | Where-Object LastWriteTimeUtc -gt $BuiltAt | Select-Object -First 1)
}

Write-Host "Python: $PythonCommand | Node.js: $(& node --version)"
Write-Host "Python dependencies: $(if ($NeedsPythonInstall) {'setup required'} else {'ready'})"
Write-Host "Frontend dependencies: $(if ($NeedsNpmInstall) {'setup required'} else {'ready'})"
Write-Host "Frontend build: $(if ($NeedsBuild) {'build required'} else {'up to date'})"

if ($Check) {
    if (Test-PortOpen) { Write-Host "Port 8765: a service is listening" } else { Write-Host "Port 8765: available" }
    Write-Host "`nEnvironment check complete. Nothing was installed or started." -ForegroundColor Green
    exit 0
}

if (Test-PortOpen) {
    try {
        $response = Invoke-WebRequest -Uri "$Url/api/projects" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "`nThe workbench is already running: $Url" -ForegroundColor Yellow
            Open-Workbench
            exit 0
        }
    } catch { }
    Stop-WithMessage "Port 8765 is used by another program. Close it and try again."
}

if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating the Python virtual environment"
    & $PythonCommand @PythonArguments -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Could not create the .venv virtual environment." }
}

if ($NeedsPythonInstall) {
    Write-Step "Installing Python dependencies (the first run may take several minutes)"
    & $VenvPython -m pip install -r (Join-Path $AppRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Python dependency installation failed. Check the network and the error above." }
    Set-Content -Path $RequirementsStamp -Value $RequirementsHash -Encoding ascii
}

if ($NeedsNpmInstall) {
    Write-Step "Installing frontend dependencies (the first run may take several minutes)"
    & npm ci --prefix $FrontendRoot
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "Frontend dependency installation failed. Check the network and the error above." }
    Set-Content -Path $FrontendStamp -Value $FrontendHash -Encoding ascii
}

if ($NeedsBuild) {
    Write-Step "Building the Web interface"
    & npm run build --prefix $FrontendRoot
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage "The frontend build failed. Check the error above." }
}

if (-not $NoBrowser) {
    $BrowserJob = Start-Job -ScriptBlock {
        param($TargetUrl)
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                $response = Invoke-WebRequest -Uri $TargetUrl -UseBasicParsing -TimeoutSec 1
                if ($response.StatusCode -eq 200) { Start-Process $TargetUrl; return }
            } catch { }
            Start-Sleep -Milliseconds 250
        }
    } -ArgumentList $Url
}

Write-Step "Starting the workbench"
Write-Host "URL: $Url"
Write-Host "Press Ctrl+C to stop the server." -ForegroundColor DarkGray
try {
    & $VenvPython (Join-Path $AppRoot "web_main.py")
} finally {
    if ($BrowserJob) { Remove-Job $BrowserJob -Force -ErrorAction SilentlyContinue }
}
