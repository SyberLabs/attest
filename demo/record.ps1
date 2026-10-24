# Runs the demo beats in order for a recording. Everything shows in the one live
# browser tab (terminal panel plus document), so this window can stay behind the browser.
# Between beats it waits $PauseSeconds for badge clicks, or press Enter to go on now.
# The live tab shows each beat's title and counts down to the next one (Set-Scene in live.ps1).
# Run from anywhere: powershell -File C:\Users\MATEO\attest\demo\record.ps1 [-PauseSeconds 25] [-WithGitHub]
param([int]$PauseSeconds = 25, [switch]$WithGitHub)

Set-Location (Split-Path -Parent $PSScriptRoot)
. "$PSScriptRoot\live.ps1"

function Pause-Beat($next) {
    Set-Scene $global:AttestSceneTitle $next $PauseSeconds
    Write-Host ""
    Write-Host "Next: $next in $PauseSeconds s (Enter to go now)"
    $deadline = (Get-Date).AddSeconds($PauseSeconds)
    while ((Get-Date) -lt $deadline) {
        if ([Console]::KeyAvailable) {
            if ([Console]::ReadKey($true).Key -eq "Enter") { break }
        }
        Start-Sleep -Milliseconds 200
    }
    Clear-Host
}

Clear-Host
& "$PSScriptRoot\beat1.ps1"
Pause-Beat "The Kane Factory: Acme CRM spec"
& "$PSScriptRoot\beat_factory.ps1"
Pause-Beat "Doc drift repaired: Vite quickstart"
& "$PSScriptRoot\beat2.ps1"
Pause-Beat "Protected promise refused: counter app"
& "$PSScriptRoot\beat2b.ps1"
if ($WithGitHub) {
    Pause-Beat "Procedural Truth on GitHub"
    & "$PSScriptRoot\beat3.ps1" -SignedIn
}
Pause-Beat "Attest's own README, proven by Kane"
& "$PSScriptRoot\beat4.ps1"
