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
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent }
    Unregister-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -Confirm:$false
}
Write-Host 'Agent registration removed. Program files, machine identity, accounting, credentials and outbox remain.'
Write-Host 'Revoke the device on the server when decommissioning it. Standalone desktop tasks and per-user ledgers were not changed.'
