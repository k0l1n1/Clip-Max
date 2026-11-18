# clip-watcher PowerShell Launcher: .\watch.ps1 <twitch_channel> [options]
param(
    [Parameter(Position=0, Mandatory=$false)]
    [string]$Channel,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingArgs
)

# Search for working Python executable, avoiding Windows Store App Execution Aliases
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "C:\Users\BIJU\AppData\Local\Programs\Python\Python312\python.exe",
    "python.exe",
    "py.exe"
)

$pythonBin = $null
foreach ($cand in $candidates) {
    if (Test-Path $cand -ErrorAction SilentlyContinue) {
        $pythonBin = $cand
        break
    }
}

if (-not $pythonBin) {
    try {
        & py -3 -c "exit(0)" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonBin = "py" }
    } catch {}
}

if (-not $pythonBin) {
    Write-Error "Python 3 was not found. Please install Python or specify its path."
    exit 1
}

if (-not $Channel) {
    Write-Host "Usage: .\watch.ps1 <twitch_channel> [options]"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\watch.ps1 shroud"
    Write-Host "  .\watch.ps1 xqc --min 5"
    Write-Host "  .\watch.ps1 tarik --min 10 --quality 720p60 --model base"
    Write-Host ""
    Write-Host "Tips:"
    Write-Host "  - Quiet chats need --min 5"
    Write-Host "  - It only works while the channel is live"
    Write-Host "  - Clip streamers who allow it"
    exit 1
}

$env:PYTHONUNBUFFERED = "1"
$scriptDir = $PSScriptRoot
$watcherScript = Join-Path $scriptDir "clip_watcher.py"

& $pythonBin $watcherScript $Channel @RemainingArgs
