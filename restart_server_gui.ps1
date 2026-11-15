# Run from normal PowerShell. The server backend stays running.
$ErrorActionPreference = 'Stop'
$pythonw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
$script = Join-Path $PSScriptRoot 'lifetime_server_gui.py'
$serverData = 'D:\Dev\lifetime-test-server-live'
if (-not (Test-Path -LiteralPath $pythonw)) { throw "Python not found: $pythonw" }
if (-not (Test-Path -LiteralPath $script)) { throw "Server GUI source not found: $script" }
if (-not (Test-Path -LiteralPath $serverData)) { throw "Server data unavailable: $serverData" }
$guis = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match '(?i)(?:^|[\\/\s"])lifetime_server_gui\.py(?:["\s]|$)' })
foreach ($gui in $guis) {
    $target = Get-Process -Id $gui.ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $target) { continue }
    try {
        # Capture the handle before stopping. A PID can remain enumerable after
        # exit while another process still holds a handle to the GUI.
        $null = $target.Handle
        if (-not $target.HasExited) {
            Stop-Process -InputObject $target -Force -ErrorAction Stop
            if (-not $target.WaitForExit(15000)) {
                throw 'The previous server GUI did not exit within 15 seconds; no duplicate was started.'
            }
        }
    } finally {
        $target.Dispose()
    }
}
$previousPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = 'windows'
    Start-Process -FilePath $pythonw -WorkingDirectory $PSScriptRoot -ArgumentList @(
        ('"{0}"' -f $script), '--directory', ('"{0}"' -f $serverData),
        '--admin-port', '8444', '--language', 'en'
    )
} finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
}
