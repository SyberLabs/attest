[CmdletBinding()]
param([switch]$ViaSyberWork)
. "$PSScriptRoot\live.ps1"
Set-Scene "The Kane Factory: Acme CRM spec"
Write-Host "The Kane Factory: spec -> Kane -> SyberWork -> worker -> Kane -> outcome"
# SyberWork dispatch (SyberLabs/SyberWork #92, checked out under .attest\syberwork) admits each
# ticket, records it, and routes it to the worker in demo\syberwork\workers.json: Attest's
# OpenAI worker (gpt-6-sol). Opt in with -ViaSyberWork; by default Attest calls that worker directly.
$dispatch = Join-Path $repo ".attest\syberwork\examples\attest_dispatch.py"
if ($ViaSyberWork -and (Test-Path $dispatch)) {
    $python = Join-Path $repo ".venv\Scripts\python.exe"
    $workers = Join-Path $repo "demo\syberwork\workers.json"
    $env:ATTEST_WORKER_CMD = "`"$python`" `"$dispatch`" --workers `"$workers`""
}
# The app is copied to a candidate workspace first; demo/app itself is never modified.
& $attest prove demo/spec.md --app demo/app --runner kane --loop --report --headless --live
exit $LASTEXITCODE
