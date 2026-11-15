# Restart (or with -Stop, only stop) the installed Lifetime Server, whichever way it was registered. Data is untouched.
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$Stop)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This script requires Windows.' }
$administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as Administrator.' }
$service = Get-Service -Name LifetimeServer -ErrorAction SilentlyContinue
$task = Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server -ErrorAction SilentlyContinue
if (-not $service -and -not $task) { throw 'Lifetime Server is not installed (no LifetimeServer service and no \Lifetime\Server task).' }
if ($service -and $PSCmdlet.ShouldProcess('LifetimeServer', $(if ($Stop) { 'Stop' } else { 'Restart' }))) {
    # Service mode runs the interpreter itself, so the service manager sees and stops the real process.
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name LifetimeServer
        $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, (New-TimeSpan -Seconds 30))
    }
    if (-not $Stop) { Start-Service -Name LifetimeServer }
    Write-Host "Lifetime Server service: $((Get-Service -Name LifetimeServer).Status)"
}
if ($task -and $PSCmdlet.ShouldProcess('\Lifetime\Server', $(if ($Stop) { 'Stop' } else { 'Restart' }))) {
    $executable = [Environment]::ExpandEnvironmentVariables([string]$task.Actions[0].Execute)
    $script = Join-Path ([string]$task.Actions[0].WorkingDirectory) 'lifetime_server.py'
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server }
    # Task modes leave a child process behind (one-file unpacker or venv launcher); end it too, see the uninstaller.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $executable -or ($_.CommandLine -and $_.CommandLine.Contains($script)) } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    if (-not $Stop) { Start-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server }
    Write-Host "Lifetime Server task: $((Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server).State)"
}
