@echo off
REM StockPulse daily auto pipeline - fetch Raw_Files, merge, combine only (no refill)
setlocal
cd /d "C:\Users\ChandanSoni\Downloads\merged_backend_test\merged_backend\meridianai-stockpulse-backend"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

if not exist "venv\Scripts\python.exe" (
  echo ERROR: venv python not found
  exit /b 1
)

if not exist "pipeline\output_files\auto_runs" mkdir "pipeline\output_files\auto_runs"

echo [%DATE% %TIME%] Starting auto_daily_run >> "pipeline\output_files\auto_runs\scheduler.log"
"venv\Scripts\python.exe" -u -m pipeline.auto_daily_run --stage combine --warehouse-mode fresh >> "pipeline\output_files\auto_runs\scheduler.log" 2>&1
set RC=%ERRORLEVEL%
echo [%DATE% %TIME%] Finished exit=%RC% >> "pipeline\output_files\auto_runs\scheduler.log"
exit /b %RC%
