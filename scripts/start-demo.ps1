param([string]$PythonExe = "", [switch]$KeepAlive)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $PythonExe) { $PythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe" }
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
foreach ($port in @(8765, 8000, 8501)) {
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
$started = @()
try {
    Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    $env:A_CLASSIFIER_URL = "http://127.0.0.1:8765/classify"
    $env:PYTHONIOENCODING = "utf-8"
    $commands = @(
        @{ Name="a"; Args=@("-m", "model.server_v031", "--port", "8765"); Health="http://127.0.0.1:8765/health" },
        @{ Name="b"; Args=@("-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "8000"); Health="http://127.0.0.1:8000/openapi.json" },
        @{ Name="ui"; Args=@("-m", "streamlit", "run", "app/ui.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"); Health="http://127.0.0.1:8501/_stcore/health" }
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
    Write-Output "Offline demo ready: http://127.0.0.1:8501"
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
}
