# Runs the four demo beats in order for a recording. Everything shows in the one live
# browser tab (terminal panel plus document), so this window can stay behind the browser.
# Between beats it waits $PauseSeconds for badge clicks, or press Enter to go on now.
# Run from anywhere: powershell -File C:\Users\MATEO\attest\demo\record.ps1 [-PauseSeconds 25]
param([int]$PauseSeconds = 25)

Set-Location (Split-Path -Parent $PSScriptRoot)

function Pause-Beat($next) {
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
Pause-Beat "beat 2: catch and fix the Vite quickstart"
& "$PSScriptRoot\beat2.ps1"
Pause-Beat "beat 2b: a refused repair (protected promise)"
& "$PSScriptRoot\beat2b.ps1"
Pause-Beat "beat 3: Procedural Truth on GitHub"
& "$PSScriptRoot\beat3.ps1" -SignedIn
Pause-Beat "beat 4: Attest's own README"
& "$PSScriptRoot\beat4.ps1"
