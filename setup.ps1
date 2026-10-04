$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
& .venv/Scripts/python.exe -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .venv/Scripts/python.exe download_models.py
if ($LASTEXITCODE -ne 0) { throw 'Face-model download failed.' }
& .venv/Scripts/python.exe download_extras.py
if ($LASTEXITCODE -ne 0) { throw 'Search-model or map download failed.' }
Write-Host 'Ready. Run start.ps1 to open Memorylane.'
