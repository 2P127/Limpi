$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

try {
    chcp 65001 > $null
}
catch {
    # Ignore code page changes on hosts that do not support chcp.
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

function Invoke-NativeOrThrow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path ".env")) {
    Write-Warning ".env file was not found. Copy .env.example to .env and fill DISCORD_TOKEN."
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment: .venv"

    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-NativeOrThrow -FilePath "py" -Arguments @("-m", "venv", ".venv")
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Invoke-NativeOrThrow -FilePath "python" -Arguments @("-m", "venv", ".venv")
    }
    else {
        throw "Python was not found. Install Python 3.10 or newer, then run this script again."
    }
}

Write-Host "Checking dependencies..."
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $venvPython -c "import aiohttp, discord, dotenv" *> $null
    $dependenciesInstalled = ($LASTEXITCODE -eq 0)
}
finally {
    $ErrorActionPreference = $oldErrorActionPreference
}

if (-not $dependenciesInstalled) {
    Write-Host "Installing dependencies from requirements.txt..."
    Invoke-NativeOrThrow -FilePath $venvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
}

Write-Host "Starting Limpi bot..."
Invoke-NativeOrThrow -FilePath $venvPython -Arguments @("bot.py")
