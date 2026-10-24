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
# the loop agent: Codex in a read-only sandbox, low reasoning effort for a one-line doc patch
$env:ATTEST_AGENT_CMD = "codex exec -s read-only --skip-git-repo-check -c model_reasoning_effort=low"

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
