$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

try {
    chcp 65001 > $null
    [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
}
catch {
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

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

$testMode = $args -contains "--test"

if ($testMode) {
    $testMessageBytes = [Convert]::FromBase64String("7YWM7Iqk7Yq4IOuqqOuTnDogRElTQ09SRF9UT0tFTl9URVNUIO2GoO2BsOydhCDsgqzsmqntlanri4jri6Qu")
    Write-Host ([System.Text.Encoding]::UTF8.GetString($testMessageBytes))
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
if ($testMode) {
    Invoke-NativeOrThrow -FilePath $venvPython -Arguments @("-m", "src.bot", "--test")
} else {
    Invoke-NativeOrThrow -FilePath $venvPython -Arguments @("-m", "src.bot")
}
