@echo off
REM One-click launcher for Windows. First run installs everything into .venv (needs internet).
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
  echo Creating Python environment...
  %PY% -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt || (echo Install failed & pause & exit /b 1)
)
.venv\Scripts\python.exe -m salesbot
pause
