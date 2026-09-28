$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $root ".env"
$tokenLine = if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^GARMIN_COACH_MOBILE_TOKEN=' } | Select-Object -First 1
}
if ($tokenLine) {
    $mobileToken = ($tokenLine -replace '^GARMIN_COACH_MOBILE_TOKEN=', '').Trim('"', "'")
} else {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $mobileToken = [Convert]::ToHexString($bytes).ToLowerInvariant()
    Add-Content -Path $envFile -Value "`nGARMIN_COACH_MOBILE_TOKEN=$mobileToken"
}
$env:GARMIN_COACH_MOBILE_TOKEN = $mobileToken
$ip = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    Select-Object -First 1 -ExpandProperty IPAddress
Write-Host ""
Write-Host "Dans l'app mobile :" -ForegroundColor Cyan
Write-Host "Serveur : http://${ip}:8765"
Write-Host "Jeton   : $mobileToken"
Write-Host ""
& (Join-Path $root ".venv\Scripts\python.exe") -m garmin_coach.dashboard --host 0.0.0.0 --port 8765 --no-browser
