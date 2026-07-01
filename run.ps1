$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Setup = Join-Path $Root "setup_environment.py"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python was not found."
    Write-Host "Install Python 3.11 or newer from https://www.python.org/downloads/"
    Write-Host "During installation, tick: Add python.exe to PATH"
    exit 1
}

python $Setup

if (-not (Test-Path $VenvPython)) {
    Write-Host "Virtual environment Python was not found after setup."
    exit 1
}

& $VenvPython (Join-Path $Root "football\fb_run_intensive.py")
