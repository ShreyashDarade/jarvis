# Starts Jarvis: builds the Go gateway, then runs it (it starts the Python AI service itself).
# Windows UI control (typing, clicking and key presses in a chosen window) is ON by default.
# Every one of those actions still asks for approval. Use -NoWindowsUi to turn it off.
# Usage:  .\start.ps1 [-NoWindowsUi]
param(
    [switch]$NoWindowsUi
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$env:JARVIS_WINDOWS_UI_CONTROL = if ($NoWindowsUi) { "0" } else { "1" }

if (-not $env:OPENAI_API_KEY) {
    Write-Error "OPENAI_API_KEY is not set in your environment."
}
if (Get-Process jarvis -ErrorAction SilentlyContinue) {
    Write-Error "Jarvis is already running. Run .\stop.ps1 first."
}

Push-Location "$root\backend"
try {
    go build -o jarvis.exe ./cmd/jarvis
    if ($LASTEXITCODE -ne 0) { throw "go build failed" }
    .\jarvis.exe
} finally {
    Pop-Location
}
