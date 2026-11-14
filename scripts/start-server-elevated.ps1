# Server backend and dashboard require access to Administrator-only server data.
param([string]$ServerDirectory = 'D:\Dev\lifetime-test-server-live')
$ErrorActionPreference = 'Stop'
trap {
    $_ | Out-File -LiteralPath (Join-Path $logDirectory 'manual-launch-error.log')
    exit 1
}
$repo = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $repo '.qa'
New-Item -ItemType Directory -Force $logDirectory | Out-Null
$pythonw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'Python 3.12 pythonw.exe was not found.' }
if (-not (Test-Path -LiteralPath $ServerDirectory -PathType Container)) { throw 'Existing server data directory was not found.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Server startup requires Administrator rights. Use try-upgrade.ps1 from normal PowerShell and accept its Windows UAC prompt.'
}
function Test-LocalPort([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($pending)
        return $true
    } catch { return $false } finally { $client.Dispose() }
}
if (-not (Test-LocalPort 8444)) {
    if (Test-LocalPort 8443) { throw 'Port 8443 is occupied but admin port 8444 is unavailable. No additional backend was started.' }
    $serverScript = Join-Path $PSScriptRoot 'manual_server_runtime.py'
    $previousPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = 'windows'
        $backend = Start-Process -FilePath $pythonw -WindowStyle Hidden -WorkingDirectory $repo -ArgumentList (
            '"{0}" console --directory "{1}" --host 0.0.0.0 --port 8443 --admin-port 8444' -f $serverScript, ($ServerDirectory.TrimEnd('\') + '\.')
        ) -RedirectStandardOutput (Join-Path $logDirectory 'manual-server-output.log') -PassThru -RedirectStandardError (Join-Path $logDirectory 'manual-server-error.log')
    } finally { $env:QT_QPA_PLATFORM = $previousPlatform }
    $deadline = (Get-Date).AddSeconds(20)
    while ($true) {
        if ($backend.HasExited) { throw 'Backend exited during startup. Read .qa\manual-server-error.log.' }
        $ready = Get-Content -LiteralPath (Join-Path $logDirectory 'manual-server-output.log') -ErrorAction SilentlyContinue | Select-String '^Lifetime Server:'
        if ($ready -and (Test-LocalPort 8444) -and (Test-LocalPort 8443)) { break }
        if ((Get-Date) -gt $deadline) { throw 'Backend did not become available. Read .qa\manual-server-error.log.' }
        Start-Sleep -Milliseconds 500
    }
}
if (-not (Test-LocalPort 8443)) { throw 'Public server port 8443 is unavailable. No GUI was restarted.' }
& (Join-Path $repo 'restart_server_gui.ps1')
