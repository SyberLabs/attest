. "$PSScriptRoot\live.ps1"
Set-Scene "Beat 1: Flask quickstart, proven by Kane"
Write-Host "Beat 1: prove the Flask quickstart"
& $attest prove demo/flask.md --runner kane --report --headless --live
exit $LASTEXITCODE
