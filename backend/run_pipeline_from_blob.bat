@echo off
REM Fetch input/ from Azure Blob, then run full pipeline
cd /d c:\Users\ChandanSoni\Downloads\zinc_london_project\merged_backend
echo.
echo === IMPORTANT: Close Excel files under input_files before continuing ===
echo.
pause
echo Syncing Blob input/ ...
python -m pipeline.runner --sync-only
if errorlevel 1 (
  echo Sync had errors. Fix locks / retry, or continue with local files?
  pause
)
echo.
echo Running pipeline stage=all ...
python -m pipeline.runner --stage all --skip-blob-sync
echo.
echo EXITCODE=%ERRORLEVEL%
pause
