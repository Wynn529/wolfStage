param([string]$PythonExe='')
$ErrorActionPreference='Stop'
$realProjectRoot=Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) { $PythonExe=Join-Path $realProjectRoot '.venv\Scripts\python.exe' }
$PythonExe=(Resolve-Path -LiteralPath $PythonExe).Path
& $PythonExe (Join-Path $PSScriptRoot 'c_live_runtime.py') --stop
if ($LASTEXITCODE -ne 0) { throw 'Could not stop verified real-mode services.' }
