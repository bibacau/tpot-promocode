@echo off
REM Docker setup only: lets the container take screenshots of the TikTok LIVE Studio comment panel.
REM Leave this window open while streaming. Needs Python 3.10+ on Windows.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .agent-venv\Scripts\python.exe (
  echo Setting up the screen agent ^(first run only^)...
  %PY% -m venv .agent-venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
.agent-venv\Scripts\python.exe -m pip install -q mss pillow || (echo Install failed & pause & exit /b 1)
.agent-venv\Scripts\python.exe -m salesbot.screen_agent --data data
pause
