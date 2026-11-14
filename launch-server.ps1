# Launch only the dashboard. This helper never restarts other processes.
param(
    [Parameter(Mandatory = $true)][string]$Directory,
    [string]$PythonPath = (Join-Path $PSScriptRoot '.venv-server\Scripts\pythonw.exe'),
    [ValidateRange(1, 65535)][int]$AdminPort = 8444
)
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'lifetime_server_gui.py'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Specify an installed Python executable using -PythonPath.' }
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw 'Server dashboard source is missing.' }
if (-not (Test-Path -LiteralPath $Directory -PathType Container)) { throw 'The server data directory does not exist.' }
$serverDirectory = (Resolve-Path -LiteralPath $Directory).Path
if ($serverDirectory.Contains('"') -or $script.Contains('"')) { throw 'Paths cannot contain double quotes.' }
$previousPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = 'windows'
    Start-Process -FilePath $PythonPath -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -ArgumentList (
        '"{0}" --directory "{1}" --admin-port {2}' -f $script, ($serverDirectory.TrimEnd('\') + '\.'), $AdminPort
    )
} finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
}
