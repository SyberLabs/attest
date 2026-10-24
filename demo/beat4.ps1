$attest = ".\.venv\Scripts\attest.exe"
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*OPENAI_API_KEY\s*=(.*)$') {
            $env:OPENAI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}
Write-Host "Beat 4: serve Attest's proven README"
& $attest serve --doc README.md --open
exit $LASTEXITCODE
