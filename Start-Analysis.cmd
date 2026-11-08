@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Follow the installation steps in README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m hedge_fund.no_cost run --open-report %*
if errorlevel 1 echo Analysis stopped. No paid fallback was used.
pause
