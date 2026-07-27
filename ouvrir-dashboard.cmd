@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERREUR : environnement Python introuvable.
    exit /b 1
)

".venv\Scripts\python.exe" -m garmin_coach.dashboard
exit /b %ERRORLEVEL%
