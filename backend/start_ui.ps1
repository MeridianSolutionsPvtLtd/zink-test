# Start StockPulse UI against Blob-backed backend (sibling folders)
$ErrorActionPreference = "Stop"
$backend = $PSScriptRoot
$frontend = Join-Path (Split-Path -Parent $PSScriptRoot) "meridianai-stockpulse-frontend"

if (-not (Test-Path (Join-Path $backend "main.py"))) {
  throw "Backend main.py not found at $backend"
}
if (-not (Test-Path (Join-Path $frontend "package.json"))) {
  throw "Frontend package.json not found at $frontend"
}

Write-Host "Starting API on http://127.0.0.1:5001 ..."
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$backend'; python -m uvicorn main:app --host 127.0.0.1 --port 5001"
)

Start-Sleep -Seconds 2
Write-Host "Starting UI on http://127.0.0.1:3000 ..."
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$frontend'; npm run dev -- --host 127.0.0.1 --port 3000"
)

Write-Host ""
Write-Host "Open: http://127.0.0.1:3000"
Write-Host "Home = Replenishments (Blob: recom_neww5)"
Write-Host "Transfers = Blob: recommended_transfers_new6"
Write-Host "API docs: http://127.0.0.1:5001/docs"
