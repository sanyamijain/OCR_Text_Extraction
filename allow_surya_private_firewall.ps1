# Run once from Administrator PowerShell only when another same-Wi-Fi device cannot connect.
$ErrorActionPreference = "Stop"
$ruleName = "Surya OCR LAN UI (Private TCP 8502)"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8502 -Profile Private | Out-Null
    Write-Host "Private-network firewall access enabled for Surya OCR on TCP port 8502."
} else { Write-Host "The Surya OCR private-network firewall rule already exists." }
