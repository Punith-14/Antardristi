# Antardrishti - start the product on this Windows machine.
#
#   .\start.ps1            build the web app if needed, start the server, open the browser
#   .\start.ps1 -Rebuild   rebuild the web app first (after changing frontend code)
#
# One server, one address: http://127.0.0.1:8000 serves both the web app and
# the API. No Vite, no reload - this is how the product runs, not how it is
# developed.

param([switch]$Rebuild)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend "venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtual environment at backend\venv. Create it first:" -ForegroundColor Red
    Write-Host "  cd backend; python -m venv venv; .\venv\Scripts\pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path (Join-Path $backend ".env"))) {
    Write-Host "backend\.env is missing. Copy backend\.env.example to backend\.env and fill it in." -ForegroundColor Red
    exit 1
}

$dist = Join-Path $frontend "dist\index.html"
if ($Rebuild -or -not (Test-Path $dist)) {
    Write-Host "Building the web app..." -ForegroundColor Cyan
    Push-Location $frontend
    # install picks up packages added since the last build (three.js and
    # satellite.js arrived with the Satellites page); it is quick when nothing changed.
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
}

Write-Host "Checking the configuration..." -ForegroundColor Cyan
Push-Location $backend
& $python -c "from dotenv import load_dotenv; load_dotenv(); from core import settings; [print('WARNING:', p) for p in settings.problems()]"

Write-Host "Starting Antardrishti on http://127.0.0.1:8000" -ForegroundColor Green
Start-Job -ScriptBlock { Start-Sleep -Seconds 4; Start-Process "http://127.0.0.1:8000" } | Out-Null
& $python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
Pop-Location
