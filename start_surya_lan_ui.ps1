$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& ".\.venv\Scripts\python.exe" ".\surya_lan_server.py" --host 0.0.0.0 --port 8502
