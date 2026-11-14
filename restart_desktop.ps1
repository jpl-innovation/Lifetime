# Run from a normal PowerShell session; restarts only the desktop GUI.
$ErrorActionPreference = 'Stop'
$pythonw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
$script = Join-Path $PSScriptRoot 'lifetime_gui.py'
if (-not (Test-Path -LiteralPath $pythonw)) { throw "Python not found: $pythonw" }
if (-not (Test-Path -LiteralPath $script)) { throw "Desktop source not found: $script" }
$guis = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match '(?i)(?:^|[\\/\s"])lifetime_gui\.py(?:["\s]|$)' })
foreach ($gui in $guis) {
    $target = Get-Process -Id $gui.ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $target) { continue }
    try {
        $null = $target.Handle
        if (-not $target.HasExited) {
            Stop-Process -InputObject $target -Force -ErrorAction Stop
            if (-not $target.WaitForExit(15000)) {
                throw 'The previous desktop GUI did not exit within 15 seconds; no duplicate was started.'
            }
        }
    } finally {
        $target.Dispose()
    }
}
$previousPlatform = $env:QT_QPA_PLATFORM
$previousData = $env:LIFETIME_DATA_DIR
try {
    $env:QT_QPA_PLATFORM = 'windows'
    $env:LIFETIME_DATA_DIR = Join-Path $env:APPDATA 'Lifetime'
    Start-Process -FilePath $pythonw -WorkingDirectory $PSScriptRoot -ArgumentList ('"{0}"' -f $script)
} finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:LIFETIME_DATA_DIR = $previousData
}
