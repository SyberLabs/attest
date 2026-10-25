. "$PSScriptRoot\live.ps1"
Set-Scene "Protected promise refused: counter app"
Write-Host "Beat 2b: the factory refuses to weaken a protected promise"
# demo/attest.policy.yaml protects "increments by one"; the counter app adds two
& $attest prove demo/counter.md --runner kane --loop --report --headless --live
exit $LASTEXITCODE
