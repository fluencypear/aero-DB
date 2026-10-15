@echo off
cd /d "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 (
  py -3 server.py
  goto done
)
python --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.8+ is required. Please install Python before starting.
  goto done
)
python server.py
:done
pause
