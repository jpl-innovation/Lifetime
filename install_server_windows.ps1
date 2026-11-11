# Run explicitly from an elevated PowerShell prompt; installs only the server edition.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Address,
    [string]$HostAddress = '0.0.0.0',
    [ValidateRange(1, 65535)][int]$Port = 8443,
    [ValidateRange(1, 65535)][int]$AdminPort = 8444,
    [ValidateSet('SourceService', 'SourceTask', 'PackagedTask')][string]$Mode = 'SourceService',
    [string]$Python = 'python',
    [string]$InstallDirectory = "$env:ProgramFiles\LifetimeServer",
    [string]$DataDirectory = "$env:ProgramData\Lifetime\Server",
    [switch]$OpenFirewall,
    [string[]]$FirewallRemoteAddress = @('LocalSubnet'),
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This installer requires Windows.' }
$administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as Administrator.' }
$uri = $null
if (-not [Uri]::TryCreate($Address, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or
    $uri.Port -ne $Port -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
    throw 'Address must be an explicit HTTPS origin whose port matches Port, for example https://server.example:8443.'
}
if ($AdminPort -eq $Port) { throw 'Public and loopback administration ports must differ.' }
if ($Mode -ne 'PackagedTask') {
    # Resolve Python before anything is copied, so a missing or per-user Python leaves the machine untouched.
    # Get-Command can return several matches (a real Python plus the Microsoft Store stub in WindowsApps);
    # the stub is not a Python, and an array would crash GetFullPath with an unreadable .NET message.
    $found = @(Get-Command $Python -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' })
    if (-not $found) { throw "Python was not found ('$Python'). Install Python 3.11 or newer for all users, or pass its full path with -Python." }
    $Python = $found[0].Source
    $userRoot = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive 'Users')).TrimEnd('\') + '\'
    if ([IO.Path]::GetFullPath($Python).StartsWith($userRoot, [StringComparison]::OrdinalIgnoreCase)) { throw "SYSTEM needs a machine-wide Python installation outside user profiles, and '$Python' is inside one. Install Python for all users (for example: winget install Python.Python.3.12 --scope machine) or pass its full path with -Python." }
}
if (Get-Service -Name LifetimeServer -ErrorAction SilentlyContinue) { throw 'Stop and uninstall the existing LifetimeServer registration before upgrading; data is preserved.' }
if (Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server -ErrorAction SilentlyContinue) { throw 'Uninstall the existing Lifetime Server task registration before upgrading; data is preserved.' }

function Protect-Directory([string]$Path) {
    if ($Path.Contains('"') -or $Path.Contains("`r") -or $Path.Contains("`n") -or $Path.StartsWith('\\')) { throw 'Use a dedicated local path without quotes or line breaks.' }
    $absolute = [IO.Path]::GetFullPath($Path)
    $ancestor = $absolute
    while ($ancestor) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Installation and data paths cannot contain links or junctions.'
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    if ($absolute.TrimEnd('\') -eq [IO.Path]::GetPathRoot($absolute).TrimEnd('\')) { throw 'A dedicated directory is required.' }
    New-Item -ItemType Directory -Path $absolute -Force | Out-Null
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetSecurityDescriptorSddlForm('D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)')
    Set-Acl -LiteralPath $absolute -AclObject $security
    return $absolute
}
foreach ($path in @($InstallDirectory, $DataDirectory)) {
    $absolute = [IO.Path]::GetFullPath($path).TrimEnd('\')
    foreach ($reserved in @($env:ProgramFiles, $env:ProgramData, $env:SystemRoot, (Join-Path $env:ProgramData 'Lifetime'), (Join-Path $env:SystemDrive 'Users'))) {
        if ($absolute -eq [IO.Path]::GetFullPath($reserved).TrimEnd('\')) { throw 'A dedicated server directory is required; a system or shared root cannot be used.' }
    }
}
if (Test-Path -LiteralPath $InstallDirectory -PathType Container) {
    # server-install.json is written last, so an install that stopped part way (no network for pip, a failed
    # service registration) has only the copied program files. Those mark the directory as ours as well,
    # otherwise the administrator could never simply run the installer again.
    if ((Get-ChildItem -LiteralPath $InstallDirectory -Force | Select-Object -First 1) -and
        -not (Test-Path -LiteralPath (Join-Path $InstallDirectory 'server-install.json') -PathType Leaf) -and
        -not (Test-Path -LiteralPath (Join-Path $InstallDirectory 'lifetime_server.py') -PathType Leaf)) { throw 'The existing nonempty install directory is not a registered Lifetime Server installation.' }
}
if (Test-Path -LiteralPath $DataDirectory -PathType Container) {
    if ((Get-ChildItem -LiteralPath $DataDirectory -Force | Select-Object -First 1) -and
        -not (Test-Path -LiteralPath (Join-Path $DataDirectory 'server.sqlite') -PathType Leaf) -and
        -not (Test-Path -LiteralPath (Join-Path $DataDirectory 'server-cert.pem') -PathType Leaf)) { throw 'The nonempty data directory must contain an existing Lifetime Server database or certificate.' }
}
if ([IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\') -eq [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')) {
    throw 'Run the installer from an extracted package outside the installation directory.'
}
$InstallDirectory = Protect-Directory $InstallDirectory
$DataDirectory = Protect-Directory $DataDirectory
# Existing descendants also need explicit permissions: disabling inheritance at
# the root alone cannot remove explicit grants from an earlier installation.
foreach ($root in @($InstallDirectory, $DataDirectory)) {
    $children = Get-ChildItem -LiteralPath $root -Force -Recurse
    foreach ($child in $children) {
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked installation or data child is refused.' }
        $security = if ($child.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
        $sddl = if ($child.PSIsContainer) { 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)' } else { 'D:P(A;;FA;;;BA)(A;;FA;;;SY)' }
        $security.SetSecurityDescriptorSddlForm($sddl)
        Set-Acl -LiteralPath $child.FullName -AclObject $security
    }
}
Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'lifetime_*.py' -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $InstallDirectory -Force
}
foreach ($name in @('requirements-server.txt', 'uninstall_server_windows.ps1', 'SECURITY.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $InstallDirectory -Force
}
foreach ($name in @('LifetimeServerRuntime.exe', 'LifetimeServerDashboard.exe')) {
    $source = Join-Path $PSScriptRoot $name
    if (Test-Path -LiteralPath $source -PathType Leaf) { Copy-Item -LiteralPath $source -Destination $InstallDirectory -Force }
}
New-Item -ItemType Directory -Path (Join-Path $InstallDirectory 'assets'), (Join-Path $InstallDirectory 'docs') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'assets\lifetime.png'), (Join-Path $PSScriptRoot 'assets\lifetime.ico') -Destination (Join-Path $InstallDirectory 'assets') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'assets\jpl_innovation.png') -Destination (Join-Path $InstallDirectory 'assets') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'assets\jpl_innovation_light.png') -Destination (Join-Path $InstallDirectory 'assets') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs\SERVER_OPERATIONS.md') -Destination (Join-Path $InstallDirectory 'docs') -Force

if ($Mode -ne 'PackagedTask') {
    # $Python was resolved and checked above, before any file was copied; this check stays as a second guard.
    $userRoot = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive 'Users')).TrimEnd('\') + '\'
    if ([IO.Path]::GetFullPath($Python).StartsWith($userRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'SYSTEM needs a machine-wide Python installation outside user profiles. Pass its full path with -Python.' }
    $environmentDir = Join-Path $InstallDirectory '.venv'
    $ErrorActionPreference = 'Continue'
    & $Python -m venv $environmentDir
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the dedicated server Python environment.' }
    $ServerPython = Join-Path $environmentDir 'Scripts\python.exe'
    & $ServerPython -m pip install --disable-pip-version-check -r (Join-Path $InstallDirectory 'requirements-server.txt') PyQt6
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install the server-only dependencies and existing Qt dashboard dependency.' }
    $ErrorActionPreference = 'Stop'
}
if ($Mode -eq 'SourceService') {
    $ErrorActionPreference = 'Continue'
    & $ServerPython (Join-Path $InstallDirectory 'lifetime_server_service.py') --startup auto install
    if ($LASTEXITCODE -ne 0) { throw 'Service registration failed. See the explicit SourceTask/PackagedTask fallback; no fallback was installed automatically.' }
    $ErrorActionPreference = 'Stop'
    $parameters = 'HKLM:\SYSTEM\CurrentControlSet\Services\LifetimeServer\Parameters'
    New-Item -Path $parameters -Force | Out-Null
    foreach ($pair in @(@('Directory', $DataDirectory), @('Address', $Address), @('Host', $HostAddress))) {
        New-ItemProperty -Path $parameters -Name $pair[0] -Value $pair[1] -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -Path $parameters -Name Port -Value $Port -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $parameters -Name AdminPort -Value $AdminPort -PropertyType DWord -Force | Out-Null
} else {
    if ($Mode -eq 'PackagedTask') {
        $executable = Join-Path $InstallDirectory 'LifetimeServerRuntime.exe'
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'PackagedTask requires LifetimeServerRuntime.exe.' }
        $prefix = ''
    } else {
        $executable = $ServerPython
        $prefix = '"' + (Join-Path $InstallDirectory 'lifetime_server.py') + '" '
    }
    foreach ($value in @($DataDirectory, $Address, $HostAddress)) {
        if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Invalid task argument.' }
    }
    $arguments = $prefix + 'console --directory "' + $DataDirectory + '" --address "' + $Address + '" --host "' + $HostAddress + '" --port ' + $Port + ' --admin-port ' + $AdminPort
    $action = New-ScheduledTaskAction -Execute $executable -Argument $arguments -WorkingDirectory $InstallDirectory
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Lifetime Server machine startup runtime' | Out-Null
}
# The configuration file contains no credentials and inherits the private DACL.
$configuration = @{ Mode = $Mode; Directory = $DataDirectory; Address = $Address; Host = $HostAddress; Port = $Port; AdminPort = $AdminPort }
$configuration | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallDirectory 'server-install.json') -Encoding UTF8
# One-code connection fallback is limited to TCP 8443 on the local subnet.
if (-not (Get-NetFirewallRule -Name 'LifetimeServer-Connect' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'LifetimeServer-Connect' -DisplayName 'Lifetime Server local connection' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -RemoteAddress LocalSubnet -Profile Domain, Private | Out-Null
}
if ($OpenFirewall -and -not (Get-NetFirewallRule -Name 'LifetimeServer-HTTPS' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'LifetimeServer-HTTPS' -DisplayName 'Lifetime Server HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress $FirewallRemoteAddress -Profile Domain, Private | Out-Null
}
if ($Start) {
    if ($Mode -eq 'SourceService') { Start-Service -Name LifetimeServer } else { Start-ScheduledTask -TaskPath '\Lifetime\' -TaskName Server }
}
Write-Host "Installed $Mode. Data: $DataDirectory. Dashboard loopback port: $AdminPort."
Write-Host 'TCP 8443 is allowed from LocalSubnet on Domain and Private networks. Use -Start or start the registered service/task explicitly.'
