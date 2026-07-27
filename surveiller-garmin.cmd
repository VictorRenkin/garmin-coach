@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERREUR : environnement Python introuvable.
    echo Consulte le fichier README.md pour installer le projet.
    exit /b 1
)

".venv\Scripts\python.exe" -m garmin_coach.cli watch
exit /b %ERRORLEVEL%
