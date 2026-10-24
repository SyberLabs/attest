$attest = ".\.venv\Scripts\attest.exe"
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*OPENAI_API_KEY\s*=(.*)$') {
            $env:OPENAI_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}
Write-Host "Beat 2: catch and fix the Vite quickstart"
& $attest prove demo/vite.md --runner kane --loop --report --open
exit $LASTEXITCODE
