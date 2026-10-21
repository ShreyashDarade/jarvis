# Stops everything Jarvis started: the gateway, the Python AI service and any sandbox containers.
# It does not close apps Jarvis opened (Windows owns those) or Docker Desktop itself.
$stopped = @()

Get-Process jarvis -ErrorAction SilentlyContinue | ForEach-Object {
    taskkill /PID $_.Id /T /F | Out-Null
    $stopped += "gateway (pid $($_.Id))"
}

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "jarvis_ai" } |
    ForEach-Object {
        taskkill /PID $_.ProcessId /T /F | Out-Null
        $stopped += "AI service (pid $($_.ProcessId))"
    }

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $candidate) { $docker = $candidate }
}
if ($docker) {
    $saved = $env:DOCKER_HOST
    Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue   # a stray DOCKER_HOST would override the context
    foreach ($ctx in @("desktop-linux", "default")) {
        $ids = & $docker --context $ctx ps -q --filter "label=jarvis=sandbox" 2>$null
        if ($LASTEXITCODE -eq 0) {
            if ($ids) { & $docker --context $ctx kill $ids | Out-Null; $stopped += "$(@($ids).Count) sandbox container(s)" }
            break
        }
    }
    if ($saved) { $env:DOCKER_HOST = $saved }
}

if ($stopped) { "Stopped: " + ($stopped -join ", ") } else { "Nothing was running." }
