$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& ".\.venv\Scripts\python.exe" ".\code\surya_lan_server.py" --host 0.0.0.0 --port 8502
