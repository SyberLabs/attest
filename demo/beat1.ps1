. "$PSScriptRoot\live.ps1"
Write-Host "Beat 1: prove the Flask quickstart"
& $attest prove demo/flask.md --runner kane --report --headless --live
exit $LASTEXITCODE
