# Registration removal only. Binary files, databases, TLS identity and backups survive.
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$RemoveFirewallRule)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This uninstaller requires Windows.' }
$administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as Administrator.' }
$service = Get-Service -Name LifetimeServer -ErrorAction SilentlyContinue
if ($service -and $PSCmdlet.ShouldProcess('LifetimeServer', 'Stop and remove this server service registration')) {
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name LifetimeServer
        $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, (New-TimeSpan -Seconds 30))
    }
    & "$env:SystemRoot\System32\sc.exe" delete LifetimeServer
    if ($LASTEXITCODE -ne 0) { throw 'Cannot remove LifetimeServer service registration.' }
}
$task = Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server -ErrorAction SilentlyContinue
if ($task -and $PSCmdlet.ShouldProcess('\Lifetime\Server', 'Stop and remove this server task registration')) {
    $executable = [Environment]::ExpandEnvironmentVariables([string]$task.Actions[0].Execute)
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server }
    # The packaged runtime is a one-file executable: it starts a second process that does the work, and stopping
    # the task ends only the first. Left alone, that process keeps the port and the program file busy, so a later
    # install or upgrade could not replace the file or start. End only processes running this task's own program.
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $executable } | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        $_.WaitForExit(10000) | Out-Null
    }
    Unregister-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server -Confirm:$false
}
# The installer always adds LifetimeServer-Connect and adds LifetimeServer-HTTPS only on request; remove both here.
foreach ($ruleName in @('LifetimeServer-Connect', 'LifetimeServer-HTTPS')) {
    if ($RemoveFirewallRule -and (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) -and
        $PSCmdlet.ShouldProcess($ruleName, 'Remove this installer-created inbound firewall rule')) {
        Remove-NetFirewallRule -Name $ruleName
    }
}
Write-Host 'Server registrations removed. Program files, data, TLS keys/certificates, administrator token and backups are preserved.'
Write-Host 'The standalone desktop and machine-agent task were not changed.'
