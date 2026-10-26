param([string]$PythonExe='', [string]$Model='deepseek-chat', [switch]$CheckOnly,
      [ValidateRange(1,65535)][int]$APort=18782,
      [ValidateRange(1,65535)][int]$BPort=18082,
      [ValidateRange(1,65535)][int]$UiPort=18514)
$ErrorActionPreference='Stop'
$realProjectRoot=Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) { $PythonExe=Join-Path $realProjectRoot '.venv\Scripts\python.exe' }
$PythonExe=(Resolve-Path -LiteralPath $PythonExe).Path
$realPriorKey=$env:DEEPSEEK_API_KEY
$realSecureKey=$null
try {
    if (-not $CheckOnly -and [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
        $realSecureKey=Read-Host 'Enter DeepSeek API Key locally (hidden, not saved)' -AsSecureString
        $realKeyPointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($realSecureKey)
        try { $env:DEEPSEEK_API_KEY=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($realKeyPointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($realKeyPointer) }
    }
    $realArguments=@((Join-Path $PSScriptRoot 'c_live_runtime.py'),'--model',$Model,
                     '--a-port',"$APort",'--b-port',"$BPort",'--ui-port',"$UiPort")
    if ($CheckOnly) { $realArguments+='--check-only' }
    & $PythonExe @realArguments
    if ($LASTEXITCODE -ne 0) { throw 'Real mode is not ready. No game was automatically started. Check the message above.' }
} finally {
    $env:DEEPSEEK_API_KEY=$realPriorKey
    if ($realSecureKey) { $realSecureKey.Dispose() }
}
