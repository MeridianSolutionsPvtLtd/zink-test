@echo off
REM Start Blob-backed StockPulse API + UI (sibling folders under merged_backend)
set "ROOT=%~dp0.."
set "BACKEND=%~dp0"
set "FRONTEND=%ROOT%\meridianai-stockpulse-frontend"

cd /d "%BACKEND%"
start "stockpulse-api" cmd /k "python -m uvicorn main:app --host 127.0.0.1 --port 5001"
timeout /t 3 /nobreak >nul
cd /d "%FRONTEND%"
start "stockpulse-ui" cmd /k "npm.cmd run dev -- --host 127.0.0.1 --port 3000"
echo.
echo Open http://127.0.0.1:3000
echo API  http://127.0.0.1:5001/docs
echo Replen = output/recom_neww5.csv
echo Transfer = output/recommended_transfers_new6.csv
