. "$PSScriptRoot\live.ps1"
# npm answers "create vite@latest" and "npm install" from its cache when the venue network is off
$env:npm_config_prefer_offline = "true"
Write-Host "Beat 2: catch and fix the Vite quickstart"
& $attest prove demo/vite.md --runner kane --loop --report --headless --live
exit $LASTEXITCODE
