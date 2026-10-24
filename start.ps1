$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\server.py"
