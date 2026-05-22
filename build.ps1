$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$botVersion = (Select-String -Path "$root\config.py" -Pattern '^BOT_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (-not $botVersion) { throw "config.py에서 BOT_VERSION을 찾을 수 없습니다." }

$iconPath = "$root\logo.ico"
if (-not (Test-Path $iconPath)) {
    throw "Icon file was not found: $iconPath"
}

Remove-Item -Recurse -Force "$root\dist", "$root\build" -ErrorAction SilentlyContinue

$addDataArgs = @()
if (Test-Path "$root\.env") {
    $addDataArgs += @("--add-data", "$root\.env;.")
}
$addDataArgs += @(
    "--add-data", "$root\logo.ico;.",
    "--add-data", "$root\img;img"
)

$pyInstallerArgs = @(
    "--onefile",
    "--windowed",
    "--icon=$iconPath",
    "--name=Limpi-$botVersion",
    "--hidden-import=bot",
    "--hidden-import=config",
    "--hidden-import=models",
    "--hidden-import=storage",
    "--hidden-import=steam_client",
    "--hidden-import=x_client",
    "--hidden-import=pm_twitter",
    "--hidden-import=discord",
    "--hidden-import=discord.ext.commands",
    "--hidden-import=discord.ext.tasks",
    "--hidden-import=discord.app_commands",
    "--hidden-import=aiohttp",
    "--hidden-import=dotenv",
    "--hidden-import=pystray._win32",
    "--hidden-import=PIL",
    "--hidden-import=PIL.Image",
    "--hidden-import=PIL.ImageDraw",
    "--collect-submodules=discord",
    "--collect-submodules=aiohttp"
)
$pyInstallerArgs += $addDataArgs
$pyInstallerArgs += "$root\launcher.py"

& "$root\.venv\Scripts\pyinstaller.exe" @pyInstallerArgs

Write-Host ""
Write-Host "Build complete: $root\dist\Limpi-$botVersion.exe"
$size = [math]::Round((Get-Item "$root\dist\Limpi-$botVersion.exe").Length / 1MB, 1)
Write-Host "Size: $size MB"

