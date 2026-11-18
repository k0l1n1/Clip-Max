# clip-watcher Studio PowerShell Launcher: .\web.ps1
param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingArgs
)

$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "C:\Users\BIJU\AppData\Local\Programs\Python\Python312\python.exe",
    "python.exe"
)

$pythonBin = $null
foreach ($cand in $candidates) {
    if (Test-Path $cand -ErrorAction SilentlyContinue) {
        $pythonBin = $cand
        break
    }
}

if (-not $pythonBin) { $pythonBin = "python" }

$env:PYTHONUNBUFFERED = "1"
$scriptDir = $PSScriptRoot
$serverScript = Join-Path $scriptDir "server.py"

Write-Host "Starting clip-watcher Studio at http://127.0.0.1:8000 ..." -ForegroundColor Cyan
& $pythonBin $serverScript @RemainingArgs
