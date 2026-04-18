$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 4188
$url = "http://127.0.0.1:$port"

function Test-AgentServer {
    try {
        $response = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-AgentServer)) {
    Start-Process -FilePath "python" -ArgumentList "server.py" -WorkingDirectory $projectRoot -WindowStyle Hidden
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 300
        if (Test-AgentServer) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        Write-Host "Nie udalo sie uruchomic lokalnego serwera Agent Gmail."
        Write-Host "Sprawdz, czy Python jest zainstalowany i czy port $port jest wolny."
        pause
        exit 1
    }
}

$edge = "$env:ProgramFiles (x86)\Microsoft\Edge\Application\msedge.exe"
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"

if (Test-Path $edge) {
    Start-Process -FilePath $edge -ArgumentList "--app=$url"
} elseif (Test-Path $chrome) {
    Start-Process -FilePath $chrome -ArgumentList "--app=$url"
} else {
    Start-Process $url
}
