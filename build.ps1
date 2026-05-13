# Limpi Bot — PyInstaller build script
# Usage: .\.venv\Scripts\powershell -File build.ps1
# Output: dist\LimpiBot.exe  (standalone, no .venv or Python needed)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# 사용자 제공 아이콘 사용 (logo.ico)
$iconPath = "$root\logo.ico"
if (-not (Test-Path $iconPath)) {
    throw "아이콘 파일이 없습니다: $iconPath"
}

Remove-Item -Recurse -Force "$root\dist", "$root\build" -ErrorAction SilentlyContinue

& "$root\.venv\Scripts\pyinstaller.exe" `
    --onefile `
    --windowed `
    --icon="$iconPath" `
    --name=Limpi `
    --add-data "$root\.env;." `
    --add-data "$root\logo.ico;." `
    --hidden-import=bot `
    --hidden-import=config `
    --hidden-import=models `
    --hidden-import=storage `
    --hidden-import=steam_client `
    --hidden-import=pm_twitter `
    --hidden-import=discord `
    --hidden-import=discord.ext.commands `
    --hidden-import=discord.ext.tasks `
    --hidden-import=discord.app_commands `
    --hidden-import=aiohttp `
    --hidden-import=dotenv `
    --hidden-import=pystray._win32 `
    --hidden-import=PIL `
    --hidden-import=PIL.Image `
    --hidden-import=PIL.ImageDraw `
    --collect-submodules=discord `
    --collect-submodules=aiohttp `
    "$root\launcher.py"

Write-Host ""
Write-Host "Build complete: $root\dist\Limpi.exe"
$size = [math]::Round((Get-Item "$root\dist\Limpi.exe").Length / 1MB, 1)
Write-Host "Size: $size MB"
