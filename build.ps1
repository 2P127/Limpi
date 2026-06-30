$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$progressActivity = "Limpi build"

function Format-BuildProgressBar {
    param([Parameter(Mandatory = $true)][int]$Percent)

    $width = 24
    $clamped = [Math]::Min(100, [Math]::Max(0, $Percent))
    $filled = [int][Math]::Round($width * ($clamped / 100))
    return ("#" * $filled) + ("-" * ($width - $filled))
}

function Write-BuildProgress {
    param(
        [Parameter(Mandatory = $true)][int]$Percent,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$CurrentOperation = "",
        [switch]$LogLine
    )

    $clamped = [Math]::Min(100, [Math]::Max(0, $Percent))
    Write-Progress `
        -Activity $progressActivity `
        -Status "$clamped% - $Status" `
        -CurrentOperation $CurrentOperation `
        -PercentComplete $clamped

    if ($LogLine) {
        $bar = Format-BuildProgressBar -Percent $clamped
        Write-Host ("[{0}] {1,3}%  {2}" -f $bar, $clamped, $Status) -ForegroundColor Cyan
    }
}

function Write-PyInstallerLogDelta {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ref]$PrintedLineCount
    )

    if (-not (Test-Path $Path)) {
        return
    }

    $lines = @(Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)
    if ($lines.Count -le $PrintedLineCount.Value) {
        return
    }

    for ($i = $PrintedLineCount.Value; $i -lt $lines.Count; $i++) {
        Write-Host $lines[$i] -ForegroundColor DarkGray
    }
    $PrintedLineCount.Value = $lines.Count
}

function Invoke-PyInstallerWithProgress {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    $logPath = Join-Path ([System.IO.Path]::GetTempPath()) "limpi-pyinstaller-$PID.log"
    Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue

    $job = Start-Job -Name "LimpiPyInstaller" -ScriptBlock {
        param($exe, $arguments, $cwd, $outputPath)

        try {
            Set-Location -LiteralPath $cwd
            & $exe @arguments *> $outputPath
            if ($null -eq $LASTEXITCODE) {
                return 0
            }
            return $LASTEXITCODE
        }
        catch {
            $_ | Out-File -FilePath $outputPath -Append -Encoding UTF8
            return 1
        }
    } -ArgumentList $Executable, $Arguments, $WorkingDirectory, $logPath

    $startedAt = Get-Date
    $printedLineCount = 0
    try {
        while ($job.State -eq "Running") {
            $elapsed = (Get-Date) - $startedAt
            $movingPercent = [Math]::Min(92, 45 + [int](($elapsed.TotalSeconds / 180) * 45))
            Write-BuildProgress `
                -Percent $movingPercent `
                -Status "PyInstaller packaging" `
                -CurrentOperation ("elapsed {0:mm\:ss}" -f $elapsed)
            Write-PyInstallerLogDelta -Path $logPath -PrintedLineCount ([ref]$printedLineCount)
            Start-Sleep -Milliseconds 750
        }

        Wait-Job $job | Out-Null
        Write-PyInstallerLogDelta -Path $logPath -PrintedLineCount ([ref]$printedLineCount)
        $exitCode = Receive-Job $job
        if ($exitCode -is [array]) {
            $exitCode = $exitCode[-1]
        }
        if ($null -eq $exitCode) {
            $exitCode = 1
        }
    }
    finally {
        Remove-Job $job -Force -ErrorAction SilentlyContinue
    }

    if ([int]$exitCode -ne 0) {
        if (Test-Path $logPath) {
            Write-Host ""
            Write-Host "PyInstaller output:" -ForegroundColor Yellow
            Get-Content -LiteralPath $logPath | ForEach-Object {
                Write-Host $_ -ForegroundColor DarkGray
            }
        }
        throw "PyInstaller failed with exit code $exitCode."
    }

    Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue
}

try {
    Write-BuildProgress -Percent 2 -Status "Preparing build" -LogLine

    $botVersion = (Select-String -Path "$root\src\core\config.py" -Pattern '^BOT_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    if (-not $botVersion) { throw "Could not find BOT_VERSION in config.py." }
    Write-BuildProgress -Percent 8 -Status "Version found: $botVersion" -LogLine

    $iconPath = "$root\logo.ico"
    if (-not (Test-Path $iconPath)) {
        throw "Icon file was not found: $iconPath"
    }
    Write-BuildProgress -Percent 14 -Status "Icon checked" -LogLine

    Remove-Item -Recurse -Force "$root\dist", "$root\build" -ErrorAction SilentlyContinue
    Write-BuildProgress -Percent 22 -Status "Old build output cleaned" -LogLine

    $addDataArgs = @()
    if (Test-Path "$root\.env") {
        $addDataArgs += @("--add-data", "$root\.env;.")
    }
    $addDataArgs += @(
        "--add-data", "$root\logo.ico;.",
        "--add-data", "$root\img;img"
    )
    Write-BuildProgress -Percent 32 -Status "Data files prepared" -LogLine

    $pyInstallerArgs = @(
        "--onefile",
        "--windowed",
        "--clean",
        "--optimize=2",
        "--log-level=WARN",
        "--icon=$iconPath",
        "--name=Limpi-$botVersion",
        "--hidden-import=src.bot",
        "--hidden-import=src.bot_constants",
        "--hidden-import=src.bot_helpers",
        "--hidden-import=src.bot_runtime",
        "--hidden-import=src.bot_views",
        "--hidden-import=src.launcher",
        "--hidden-import=src.core.config",
        "--hidden-import=src.core.models",
        "--hidden-import=src.core.storage",
        "--hidden-import=src.clients.chzzk_client",
        "--hidden-import=src.clients.steam_client",
        "--hidden-import=src.clients.x_client",
        "--hidden-import=src.clients.youtube_client",
        "--hidden-import=ego",
        "--hidden-import=discord",
        "--hidden-import=discord.ext.commands",
        "--hidden-import=discord.ext.tasks",
        "--hidden-import=discord.app_commands",
        "--hidden-import=aiohttp",
        "--hidden-import=playwright",
        "--hidden-import=dotenv",
        "--hidden-import=pystray._win32",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=PIL.ImageDraw",
        "--exclude-module=pystray._appindicator",
        "--exclude-module=pystray._darwin",
        "--exclude-module=pystray._gtk",
        "--exclude-module=pystray._xorg",
        "--exclude-module=pystray._util.gtk",
        "--exclude-module=pystray._util.notify_dbus",
        "--collect-submodules=discord",
        "--collect-submodules=aiohttp",
        "--collect-submodules=playwright",
        "--collect-submodules=src"
    )
    $pyInstallerArgs += $addDataArgs
    $pyInstallerArgs += "$root\src\launcher.py"
    Write-BuildProgress -Percent 42 -Status "PyInstaller options prepared" -LogLine

    Invoke-PyInstallerWithProgress `
        -Executable "$root\.venv\Scripts\pyinstaller.exe" `
        -Arguments $pyInstallerArgs `
        -WorkingDirectory $root
    Write-BuildProgress -Percent 95 -Status "Checking build output" -LogLine

    Write-Host ""
    Write-Host "Build complete: $root\dist\Limpi-$botVersion.exe"
    $size = [math]::Round((Get-Item "$root\dist\Limpi-$botVersion.exe").Length / 1MB, 1)
    Write-Host "Size: $size MB"
    Write-BuildProgress -Percent 100 -Status "Build complete" -LogLine
    Write-Progress -Activity $progressActivity -Completed
}
catch {
    Write-Progress -Activity $progressActivity -Completed
    throw
}
