# Shared by the beat scripts: load the API key, pick the loop agent, and make sure the
# single live page is being served on localhost:4848 and open in one browser tab.

$script:attest = Join-Path $PSScriptRoot "..\.venv\Scripts\attest.exe"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*OPENAI_API_KEY\s*=(.*)$') {
            $env:OPENAI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}
# the loop agent: the OpenAI API writes the doc patch (attest.loop.call_openai_agent)
$env:ATTEST_AGENT_CMD = "openai"

# The live tab shows the beat title and a countdown to the next beat from .attest\scene.json.
$global:AttestScenePath = Join-Path $repo ".attest\scene.json"
function Set-Scene([string]$title, [string]$next = "", [int]$seconds = 0) {
    try {
        $global:AttestSceneTitle = $title
        $until = 0
        if ($seconds -gt 0) { $until = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + $seconds }
        $json = @{ title = $title; next = $next; until = $until } | ConvertTo-Json -Compress
        $dir = Split-Path -Parent $global:AttestScenePath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $tmp = "$($global:AttestScenePath).tmp"
        [System.IO.File]::WriteAllText($tmp, $json)
        Move-Item -LiteralPath $tmp -Destination $global:AttestScenePath -Force
    } catch {
        # the scene title is decoration; never stop the demo for it
    }
}

function Test-Live {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:4848/index.html" -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Live)) {
    Start-Process -FilePath $script:attest -ArgumentList "serve", "--live", "--port", "4848" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Live) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    Start-Process "http://localhost:4848/"
}
