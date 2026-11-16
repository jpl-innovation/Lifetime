# Registration removal only. Binary files, databases, TLS identity and backups survive.
[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$RemoveFirewallRule)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This uninstaller requires Windows.' }
$administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as Administrator.' }
# The dashboard is not part of the service or task: left open, its windows keep this installation's program files in
# use and a later install stops on a locked file. End only dashboards running from this installation (the packaged
# LifetimeServerDashboard.exe and its one-file child, or an interpreter running its lifetime_server_gui.py).
$installRoot = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'server-install.json') -PathType Leaf) { $PSScriptRoot } else {
    $serverTask = Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server -ErrorAction SilentlyContinue
    if ($serverTask -and $serverTask.Actions[0].WorkingDirectory) { [string]$serverTask.Actions[0].WorkingDirectory } else { Join-Path $env:ProgramFiles 'LifetimeServer' }
}
$dashboard = Join-Path $installRoot 'LifetimeServerDashboard.exe'
$dashboardScript = Join-Path $installRoot 'lifetime_server_gui.py'
if ($PSCmdlet.ShouldProcess($installRoot, 'Close the server dashboard windows running from this installation')) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $dashboard -or ($_.CommandLine -and $_.CommandLine.Contains($dashboardScript)) } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
}
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
    $script = Join-Path ([string]$task.Actions[0].WorkingDirectory) 'lifetime_server.py'
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server }
    # Stopping the task ends only the process it started, and in both task modes the work is done by a second one:
    # the packaged one-file runtime unpacks into a child, and a venv python.exe is a launcher that starts the real
    # interpreter as a child. Left alone, that child keeps the port and the files busy. End only this task's own
    # program and interpreters running this installation's lifetime_server.py.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $executable -or ($_.CommandLine -and $_.CommandLine.Contains($script)) } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
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
$shortcutPath = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Lifetime Server.lnk'
if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) { Remove-Item -LiteralPath $shortcutPath -Force }
Write-Host 'Server registrations removed. Program files, data, TLS keys/certificates, administrator token and backups are preserved.'
Write-Host 'The standalone desktop and machine-agent task were not changed.'
