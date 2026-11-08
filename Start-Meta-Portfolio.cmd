@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Follow README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -m hedge_fund.no_cost macro --open-report %*
if errorlevel 1 echo Research stopped. No paid fallback was used.
pause
