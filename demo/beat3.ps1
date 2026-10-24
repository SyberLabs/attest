param(
    [string]$Card = "github-secret-scanning",
    [string]$StopBefore = "",
    [string]$Ask = "",
    [switch]$SignedIn
)

. "$PSScriptRoot\live.ps1"
Write-Host "Beat 3: prove a Procedural Truth answer"
if ($Ask) {
    & $attest ask $Ask --card $Card
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
$arguments = @("prove", "demo/answers/$Card.md", "--runner", "kane", "--loop", "--report", "--live")
if ($SignedIn) {
    $endpoint = "http://127.0.0.1:9333"
    $versionUrl = "$endpoint/json/version"
    $reachable = $false
    try {
        Invoke-WebRequest -Uri $versionUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
        $reachable = $true
    } catch {}

    if (-not $reachable) {
        $chromeCandidates = @(
            (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
            (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
        )
        $chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
        if (-not $chrome) {
            Write-Error "Google Chrome was not found in Program Files, Program Files (x86), or LocalAppData."
            exit 1
        }
        $profile = Join-Path $env:USERPROFILE ".testmuai\kaneai\chrome-profiles\kane-user"
        $chromeArgs = '--user-data-dir="{0}" --remote-debugging-port=9333 --no-first-run about:blank' -f $profile
        Start-Process -FilePath $chrome -ArgumentList $chromeArgs

        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline) {
            try {
                Invoke-WebRequest -Uri $versionUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
                $reachable = $true
                break
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
    }
    if (-not $reachable) {
        Write-Error "Chrome debugging endpoint $versionUrl did not respond within 20 seconds."
        exit 1
    }
    $arguments += @("--cdp-endpoint", $endpoint)
}
if (-not $StopBefore -and $Card -eq "github-secret-scanning") {
    $StopBefore = "c09"
}
if ($StopBefore) {
    $arguments += @("--stop-before", $StopBefore)
}
& $attest @arguments
exit $LASTEXITCODE
