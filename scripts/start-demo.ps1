param([string]$PythonExe = "", [switch]$KeepAlive,
      [ValidateRange(1,65535)][int]$APort=8765,
      [ValidateRange(1,65535)][int]$BPort=8000,
      [ValidateRange(1,65535)][int]$UiPort=8501)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $PythonExe) { $PythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe" }
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$demoPorts = @($APort,$BPort,$UiPort)
if (@($demoPorts | Select-Object -Unique).Count -ne 3) { throw 'A, B and UI ports must be different.' }
foreach ($port in $demoPorts) {
    $probe = New-Object System.Net.Sockets.TcpClient
    try {
        $probe.Connect("127.0.0.1", $port)
        throw "Port $port is already in use. Stop the previous demo first."
    } catch [System.Net.Sockets.SocketException] {
        # A refused connection means this port can be used.
    } finally { $probe.Dispose() }
}
$logRoot = Join-Path $projectRoot ".local"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$previousKey = $env:DEEPSEEK_API_KEY
$previousClassifier = $env:A_CLASSIFIER_URL
$previousEncoding = $env:PYTHONIOENCODING
$demoNames = @('B_API_URL','C_SERVICE_CONTRACT','C_RUNS_DIR','C_JOURNAL_DIR','C_B_REVISION')
$previousDemo = @{}
foreach ($name in $demoNames) { $previousDemo[$name]=[Environment]::GetEnvironmentVariable($name,'Process') }
$started = @()
try {
    Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    $env:A_CLASSIFIER_URL = "http://127.0.0.1:$APort/classify"
    $env:PYTHONIOENCODING = "utf-8"
    $env:B_API_URL = "http://127.0.0.1:$BPort"
    Remove-Item Env:C_SERVICE_CONTRACT -ErrorAction SilentlyContinue
    $env:C_B_REVISION = '3898b95'
    $env:C_RUNS_DIR = Join-Path $projectRoot 'runs'
    $env:C_JOURNAL_DIR = Join-Path $logRoot 'c_operations'
    $commands = @(
        @{ Name="a"; Args=@("-m", "model.server_v031", "--port", "$APort"); Health="http://127.0.0.1:$APort/health" },
        @{ Name="b"; Args=@("-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "$BPort"); Health="http://127.0.0.1:$BPort/openapi.json" },
        @{ Name="ui"; Args=@("-m", "streamlit", "run", "app/ui.py", "--server.address", "127.0.0.1", "--server.port", "$UiPort", "--server.headless", "true", "--browser.gatherUsageStats", "false"); Health="http://127.0.0.1:$UiPort/_stcore/health" }
    )
    foreach ($command in $commands) {
        $process = Start-Process -FilePath $PythonExe -ArgumentList $command.Args -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot ($command.Name + ".out.log")) -RedirectStandardError (Join-Path $logRoot ($command.Name + ".err.log"))
        $started += @{ Id=$process.Id; StartTime=$process.StartTime.ToUniversalTime().ToString("o"); Name=$command.Name }
        $ready = $false
        for ($attempt=0; $attempt -lt 40; $attempt++) {
            if ($process.HasExited) { throw ($command.Name + " exited. See .local logs.") }
            try {
                $null = Invoke-WebRequest -Uri $command.Health -UseBasicParsing -TimeoutSec 1
                $ready = $true
                break
            } catch { Start-Sleep -Milliseconds 250 }
        }
        if (-not $ready) { throw ($command.Name + " did not become ready. See .local logs.") }
    }
    $started | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot "processes.json") -Encoding UTF8
    Write-Output "Offline demo ready: http://127.0.0.1:$UiPort"
    if ($KeepAlive) {
        Write-Output "Keeping the demo session alive."
        while ($true) { Start-Sleep -Seconds 1 }
    }
} catch {
    foreach ($record in $started) { Stop-Process -Id $record.Id -ErrorAction SilentlyContinue }
    throw
} finally {
    $env:DEEPSEEK_API_KEY = $previousKey
    $env:A_CLASSIFIER_URL = $previousClassifier
    $env:PYTHONIOENCODING = $previousEncoding
    foreach ($name in $demoNames) { [Environment]::SetEnvironmentVariable($name,$previousDemo[$name],'Process') }
}
