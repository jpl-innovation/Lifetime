# Preserve machine identity, accounting, consent, credentials and pending outbox.
[CmdletBinding(SupportsShouldProcess = $true)]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This uninstaller requires Windows.' }
$administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as Administrator.' }
$task = Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -ErrorAction SilentlyContinue
if ($task -and $PSCmdlet.ShouldProcess('\Lifetime\Agent', 'Stop and remove this machine-agent task registration')) {
    $executable = [Environment]::ExpandEnvironmentVariables([string]$task.Actions[0].Execute)
    $script = Join-Path ([string]$task.Actions[0].WorkingDirectory) 'lifetime_agent.py'
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent }
    # Stopping the task ends only the process it started; the work is done by a child (the packaged one-file program
    # unpacks into one, a venv python.exe starts the real interpreter as one). End only this installation's agent,
    # as uninstall_server_windows.ps1 does for the server, so nothing keeps running after the uninstall.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $executable -or ($_.CommandLine -and $_.CommandLine.Contains($script)) } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    Unregister-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -Confirm:$false
}
Write-Host 'Agent registration removed. Program files, machine identity, accounting, credentials and outbox remain.'
Write-Host 'Revoke this computer on the server when it is retired. The desktop Lifetime app and its awake-time history were not changed.'
