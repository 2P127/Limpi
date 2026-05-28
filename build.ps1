$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$botVersion = (Select-String -Path "$root\src\core\config.py" -Pattern '^BOT_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
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
    "--hidden-import=src.bot",
    "--hidden-import=src.launcher",
    "--hidden-import=src.core.config",
    "--hidden-import=src.core.models",
    "--hidden-import=src.core.storage",
    "--hidden-import=src.clients.chzzk_client",
    "--hidden-import=src.clients.steam_client",
    "--hidden-import=src.clients.x_client",
    "--hidden-import=src.clients.youtube_client",
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
    "--collect-submodules=aiohttp",
    "--collect-submodules=src"
)
$pyInstallerArgs += $addDataArgs
$pyInstallerArgs += "$root\src\launcher.py"

& "$root\.venv\Scripts\pyinstaller.exe" @pyInstallerArgs

Write-Host ""
Write-Host "Build complete: $root\dist\Limpi-$botVersion.exe"
$size = [math]::Round((Get-Item "$root\dist\Limpi-$botVersion.exe").Length / 1MB, 1)
Write-Host "Size: $size MB"

