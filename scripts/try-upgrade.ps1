# Run from normal PowerShell. Only the server child requests elevation.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $repo '.qa'
New-Item -ItemType Directory -Force $logDirectory | Out-Null
$serverHelper = Join-Path $PSScriptRoot 'start-server-elevated.ps1'
# The server's protected data and admin token require Administrator rights.
# Keep the desktop launch in this normal-user parent process.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction Stop | Out-Null
$serverLaunch = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -ArgumentList (
    '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $serverHelper
)
$serverLaunch.WaitForExit()
if ($serverLaunch.ExitCode -ne 0) {
    foreach ($logName in @('manual-launch-error.log', 'manual-server-error.log')) {
        $logPath = Join-Path $logDirectory $logName
        if (Test-Path -LiteralPath $logPath) {
            Get-Content -LiteralPath $logPath -Tail 60 | Write-Host
        }
    }
    throw "Server launch failed. Read $logDirectory\manual-launch-error.log and manual-server-error.log."
}
& (Join-Path $repo 'restart_desktop.ps1')
Write-Host 'Both GUIs launched. Choose Add device on the server, then paste its code into the desktop Connect page.'
