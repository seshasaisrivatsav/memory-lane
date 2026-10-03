$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { throw 'Run setup.ps1 first.' }
$running = $false
try {
    $status = Invoke-RestMethod 'http://127.0.0.1:4317/api/status' -TimeoutSec 2
    $running = $null -ne $status.stats
} catch { }
if ($running) { Start-Process 'http://127.0.0.1:4317'; exit }
$server = Start-Process -FilePath "$PSScriptRoot/.venv/Scripts/python.exe" -ArgumentList 'app.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput "$PSScriptRoot/server.log" -RedirectStandardError "$PSScriptRoot/server-error.log" -PassThru
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $status = Invoke-RestMethod 'http://127.0.0.1:4317/api/status' -TimeoutSec 1
        if ($null -ne $status.stats) { Start-Process 'http://127.0.0.1:4317'; exit }
    } catch { Start-Sleep -Milliseconds 300 }
}
throw 'Memorylane did not start. Check server-error.log.'
