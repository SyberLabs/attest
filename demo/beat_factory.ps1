. "$PSScriptRoot\live.ps1"
Set-Scene "The Kane Factory: Acme CRM spec"
Write-Host "The Kane Factory: spec -> Kane -> SyberWork -> worker -> Kane -> outcome"
# The worker is pluggable: ATTEST_WORKER_CMD may point at SyberWork dispatch; the default is the OpenAI API.
# The app is copied to a candidate workspace first; demo/app itself is never modified.
& $attest prove demo/spec.md --app demo/app --runner kane --loop --report --headless --live
exit $LASTEXITCODE
