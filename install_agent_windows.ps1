# Machine-wide agent only. Never reads or merges a per-user ledger implicitly.
[CmdletBinding()]
param(
    [ValidateSet('Source', 'Packaged')][string]$Mode = 'Source',
    [string]$Python = 'python',
    [string]$InstallDirectory = "$env:ProgramFiles\LifetimeAgent",
    [string]$DataDirectory = "$env:ProgramData\Lifetime\Agent",
    [string]$BundleFile,
    [switch]$EnableReporting,
    [switch]$TrackHistory,
    [switch]$ResourceReadings,
    [switch]$HealthReadings,
    [switch]$PowerControl,
    [string]$DisplayName = $env:COMPUTERNAME,
    [string]$ImportLedger,
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This installer requires Windows.' }
$administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as Administrator.' }
if ([bool]$BundleFile -ne [bool]$EnableReporting) { throw 'Reporting requires both -EnableReporting and -BundleFile from the selected company server.' }
if (($TrackHistory -or $ResourceReadings -or $HealthReadings -or $PowerControl) -and -not $EnableReporting) { throw 'Optional reporting categories require explicit server enrollment.' }
if (Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -ErrorAction SilentlyContinue) { throw 'Stop and uninstall the existing Lifetime agent task registration before upgrading; data is preserved.' }
function Protect-Directory([string]$Path) {
    if ($Path.Contains('"') -or $Path.Contains("`r") -or $Path.Contains("`n") -or $Path.StartsWith('\\')) { throw 'Use a dedicated local path without quotes or line breaks.' }
    $absolute = [IO.Path]::GetFullPath($Path)
    $ancestor = $absolute
    while ($ancestor) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Installation and data paths cannot contain links or junctions.' }
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
        if ($absolute -eq [IO.Path]::GetFullPath($reserved).TrimEnd('\')) { throw 'A dedicated agent directory is required; a system or shared root cannot be used.' }
    }
}
if (Test-Path -LiteralPath $InstallDirectory -PathType Container) {
    if ((Get-ChildItem -LiteralPath $InstallDirectory -Force | Select-Object -First 1) -and -not (Test-Path -LiteralPath (Join-Path $InstallDirectory 'lifetime_agent.py') -PathType Leaf)) { throw 'The existing nonempty install directory is not a Lifetime Agent installation.' }
}
if (Test-Path -LiteralPath $DataDirectory -PathType Container) {
    if ((Get-ChildItem -LiteralPath $DataDirectory -Force | Select-Object -First 1) -and -not (Test-Path -LiteralPath (Join-Path $DataDirectory 'machine.sqlite') -PathType Leaf)) { throw 'The nonempty data directory must contain an existing Lifetime machine database.' }
}
if ([IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\') -eq [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')) { throw 'Extract the package outside the target installation directory.' }
$InstallDirectory = Protect-Directory $InstallDirectory
$DataDirectory = Protect-Directory $DataDirectory
foreach ($root in @($InstallDirectory, $DataDirectory)) {
    foreach ($child in (Get-ChildItem -LiteralPath $root -Force -Recurse)) {
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked installation or data child is refused.' }
        $security = if ($child.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
        $sddl = if ($child.PSIsContainer) { 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)' } else { 'D:P(A;;FA;;;BA)(A;;FA;;;SY)' }
        $security.SetSecurityDescriptorSddlForm($sddl)
        Set-Acl -LiteralPath $child.FullName -AclObject $security
    }
}
Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'lifetime_*.py' -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $InstallDirectory -Force }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall_agent_windows.ps1') -Destination $InstallDirectory -Force
if ($Mode -eq 'Packaged') {
    $source = Join-Path $PSScriptRoot 'LifetimeAgent.exe'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Packaged mode requires LifetimeAgent.exe.' }
    Copy-Item -LiteralPath $source -Destination $InstallDirectory -Force
    $executable = Join-Path $InstallDirectory 'LifetimeAgent.exe'
    $prefixArguments = @()
    $taskPrefix = ''
} else {
    # The machine agent uses only the standard library. No server requirements,
    # cryptography, pywin32 or Qt packages are installed in this environment.
    $environmentDir = Join-Path $InstallDirectory '.venv'
    $command = Get-Command $Python -CommandType Application -ErrorAction Stop
    $Python = $command.Source
    $userRoot = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive 'Users')).TrimEnd('\') + '\'
    if ([IO.Path]::GetFullPath($Python).StartsWith($userRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'SYSTEM needs a machine-wide Python installation outside user profiles. Pass its full path with -Python.' }
    $ErrorActionPreference = 'Continue'
    & $Python -m venv $environmentDir
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the dedicated machine-agent Python environment.' }
    $ErrorActionPreference = 'Stop'
    $executable = Join-Path $environmentDir 'Scripts\python.exe'
    $script = Join-Path $InstallDirectory 'lifetime_agent.py'
    $prefixArguments = @($script)
    $taskPrefix = '"' + $script + '" '
}
$arguments = @('--directory', $DataDirectory)
if (-not (Test-Path -LiteralPath (Join-Path $DataDirectory 'machine.sqlite') -PathType Leaf)) { $arguments += '--initialize' }
if ($ImportLedger) { $arguments += @('--import-ledger', [IO.Path]::GetFullPath($ImportLedger)) }
if ($EnableReporting) {
    Write-Host 'Enrollment sends awake-time source totals/deltas to the server pinned by this bundle, after approval.'
    Write-Host "Optional dated history: $TrackHistory; optional OS resource readings: $ResourceReadings. No employee account is needed."
    $arguments += @('--enroll-bundle-file', [IO.Path]::GetFullPath($BundleFile))
    if ($TrackHistory) { $arguments += '--policy-history' }
    if ($ResourceReadings) { $arguments += '--policy-resources' }
    if ($HealthReadings) { $arguments += '--policy-health' }
    if ($PowerControl) { $arguments += '--policy-control' }
    $arguments += @('--display-name', $DisplayName)
}
$ErrorActionPreference = 'Continue'
& $executable @prefixArguments @arguments
if ($LASTEXITCODE -ne 0) { throw 'Agent initialization, explicit import or enrollment failed; no startup task was registered. Existing data was preserved.' }
$ErrorActionPreference = 'Stop'
foreach ($value in @($InstallDirectory, $DataDirectory)) {
    if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Invalid task path.' }
}
$taskArguments = $taskPrefix + '--directory "' + $DataDirectory + '" --run'
$action = New-ScheduledTaskAction -Execute $executable -Argument $taskArguments -WorkingDirectory $InstallDirectory
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Lifetime machine capture and explicitly enrolled reporting' | Out-Null
if ($Start) { Start-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent }
Write-Host "Agent installed for SYSTEM startup. Machine data: $DataDirectory"
Write-Host 'No inbound firewall rules were created. Per-user desktop ledgers and desktop startup registrations were not changed.'
