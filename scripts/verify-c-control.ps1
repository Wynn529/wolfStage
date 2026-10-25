param([string]$PythonExe="", [switch]$FullSuite)
$ErrorActionPreference="Stop"
$projectRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $PythonExe) { $PythonExe=Join-Path $projectRoot ".venv\Scripts\python.exe" }
$PythonExe=(Resolve-Path -LiteralPath $PythonExe).Path
$tempRoot=Join-Path $projectRoot ".local\c_verify_tmp"
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot "evidence") | Out-Null
$names=@("DEEPSEEK_API_KEY","A_CLASSIFIER_URL","C_SERVICE_CONTRACT","C_RUNS_DIR","C_JOURNAL_DIR","TEMP","TMP","PYTHONIOENCODING")
$saved=@{}
foreach ($name in $names) { $saved[$name]=[Environment]::GetEnvironmentVariable($name,"Process") }
try {
    foreach ($name in @("DEEPSEEK_API_KEY","A_CLASSIFIER_URL","C_SERVICE_CONTRACT","C_RUNS_DIR","C_JOURNAL_DIR")) {
        [Environment]::SetEnvironmentVariable($name,$null,"Process")
    }
    $env:TEMP=$tempRoot
    $env:TMP=$tempRoot
    $env:PYTHONIOENCODING="utf-8"
    $arguments=@("-m","pytest","-q","--junitxml=evidence/c_control_verify.xml")
    if (-not $FullSuite) { $arguments+=@("tests/test_ui_control.py","tests/test_ui.py") }
    & $PythonExe @arguments *> (Join-Path $projectRoot "evidence\c_control_verify.log")
    $code=$LASTEXITCODE
    Get-Content -LiteralPath (Join-Path $projectRoot "evidence\c_control_verify.log") -Tail 24
    if ($code -ne 0) { throw "C control verification failed. See evidence/c_control_verify.log." }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$saved[$name],"Process") }
}
