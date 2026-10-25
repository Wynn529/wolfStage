$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $projectRoot ".local\processes.json"
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output "No demo process record."; exit }
foreach ($record in (Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json)) {
    $process = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().ToString("o") -eq $record.StartTime) {
        Stop-Process -Id $record.Id
    }
}
Remove-Item -LiteralPath $recordPath
Write-Output "Demo stopped. Logs retained in .local."
