@echo off
setlocal
if "%~1"=="" (
  echo Glisse l'export Strava ZIP sur ce fichier ou indique son chemin.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m garmin_coach.cli import-strava "%~1"
pause
