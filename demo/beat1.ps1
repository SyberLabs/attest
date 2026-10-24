$attest = ".\.venv\Scripts\attest.exe"
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*OPENAI_API_KEY\s*=(.*)$') {
            $env:OPENAI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}
Write-Host "Beat 1: prove the Flask quickstart"
& $attest prove demo/flask.md --runner kane --report --open
exit $LASTEXITCODE
