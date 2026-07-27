$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Environnement Python introuvable. Suis d'abord les instructions du README."
}

Set-Location -LiteralPath $projectDir
& $python -m garmin_coach.cli login
exit $LASTEXITCODE
