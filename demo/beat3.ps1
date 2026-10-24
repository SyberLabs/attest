param(
    [string]$Card = "github-secret-scanning",
    [string]$StopBefore = "",
    [string]$Ask = ""
)

$attest = ".\.venv\Scripts\attest.exe"
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*OPENAI_API_KEY\s*=(.*)$') {
            $env:OPENAI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}
Write-Host "Beat 3: prove a Procedural Truth answer"
if ($Ask) {
    & $attest ask $Ask --card $Card
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
$arguments = @("prove", "demo/answers/$Card.md", "--runner", "kane", "--loop", "--report", "--open")
if ($StopBefore) {
    $arguments += @("--stop-before", $StopBefore)
}
& $attest @arguments
exit $LASTEXITCODE
