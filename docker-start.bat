@echo off
REM Builds and starts the app in Docker Desktop, then opens the dashboard.
cd /d "%~dp0"
docker compose up -d --build || (echo Is Docker Desktop running? & pause & exit /b 1)
timeout /t 5 >nul
start http://127.0.0.1:8765/
echo Dashboard: http://127.0.0.1:8765/   (stop with: docker compose down)
pause
