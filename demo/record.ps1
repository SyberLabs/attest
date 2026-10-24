# Runs the four demo beats in order for a recording. Press Enter between beats;
# the pause is the time to click a badge on the page the beat just opened.
# Run from anywhere: powershell -File C:\Users\MATEO\attest\demo\record.ps1

Set-Location (Split-Path -Parent $PSScriptRoot)

function Pause-Beat($next) {
    Write-Host ""
    Read-Host "Press Enter for $next" | Out-Null
    Clear-Host
}

Clear-Host
& "$PSScriptRoot\beat1.ps1"
Pause-Beat "beat 2: catch and fix the Vite quickstart"
& "$PSScriptRoot\beat2.ps1"
Pause-Beat "beat 3: Procedural Truth on GitHub"
& "$PSScriptRoot\beat3.ps1" -SignedIn
Pause-Beat "beat 4: Attest's own README"
& "$PSScriptRoot\beat4.ps1"
