. "$PSScriptRoot\live.ps1"
Set-Scene "Attest's own README, proven by Kane"
Write-Host "Beat 4: Attest's own README, proven by Kane"
# point the live tab at the README's proven page; it was proven by Kane in the self-check
& (Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe") -c "import json; from pathlib import Path; from attest.ingest import ingest; from attest.pipeline import out_dir_for; d = out_dir_for(ingest('README.md'), Path('.attest')); assert (d / 'index.html').exists(), 'README not proven yet'; Path('.attest/latest.json').write_text(json.dumps({'page': d.name + '/index.html', 'running': False, 'now': 'Proof complete'}), encoding='utf-8'); print('README page:', d / 'index.html')"
exit $LASTEXITCODE
