# Machine-wide agent only. Never reads or merges a per-user ledger implicitly.
# Runs without prompts, so it also suits GPO startup scripts, Intune and other scripted deployment:
#   exit 0 installed, 1 unexpected failure, 2 invalid parameters, 3 server unreachable or not the server this
#   code belongs to, 4 code refused by the server (used, expired or revoked), 5 not run as administrator or
#   not Windows, 6 an agent is already installed (nothing changed).
# A log (no codes or other secrets) is appended to install.log in the data directory.
[CmdletBinding()]
param(
    [ValidateSet('Source', 'Packaged')][string]$Mode = 'Source',
    [string]$Python = 'python',
    [string]$InstallDirectory = "$env:ProgramFiles\LifetimeAgent",
    [string]$DataDirectory = "$env:ProgramData\Lifetime\Agent",
    [string]$BundleFile,
    [string]$ConnectionLine,
    [string]$PairingCode,
    [string]$Server,
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
$script:ExitCode = 0
$script:LogLines = [Collections.Generic.List[string]]::new()
$script:LogDirectory = $null
function Write-Log([string]$Message) {
    $script:LogLines.Add((Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz') + ' ' + $Message)
    Write-Host $Message
}
function Stop-Install([int]$Code, [string]$Message) {
    $script:ExitCode = $Code
    throw $Message
}
function Save-Log {
    # The data directory is readable by administrators and SYSTEM only; before it exists, use TEMP.
    $path = if ($script:LogDirectory) { Join-Path $script:LogDirectory 'install.log' } else { Join-Path $env:TEMP 'LifetimeAgent-install.log' }
    try {
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and (Get-Item -LiteralPath $path).Length -gt 256KB) { Remove-Item -LiteralPath $path -Force }
        Add-Content -LiteralPath $path -Value $script:LogLines -Encoding UTF8
    } catch {
        Write-Host 'The installation log could not be written.'
    }
}
function Protect-Directory([string]$Path) {
    if ($Path.Contains('"') -or $Path.Contains("`r") -or $Path.Contains("`n") -or $Path.StartsWith('\\')) { Stop-Install 2 'Use a dedicated local path without quotes or line breaks.' }
    $absolute = [IO.Path]::GetFullPath($Path)
    $ancestor = $absolute
    while ($ancestor) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { Stop-Install 2 'Installation and data paths cannot contain links or junctions.' }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    if ($absolute.TrimEnd('\') -eq [IO.Path]::GetPathRoot($absolute).TrimEnd('\')) { Stop-Install 2 'A dedicated directory is required.' }
    New-Item -ItemType Directory -Path $absolute -Force | Out-Null
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetSecurityDescriptorSddlForm('D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)')
    Set-Acl -LiteralPath $absolute -AclObject $security
    return $absolute
}
function Invoke-Agent([string]$Executable, [string[]]$Arguments, [string]$Secret) {
    # A connection line or code goes through standard input, never the command line other users can read.
    $ErrorActionPreference = 'Continue'
    $OutputEncoding = [Text.ASCIIEncoding]::new()      # plain ASCII on the pipe: no byte-order mark before the line
    if ($Secret) { $Secret | & $Executable @Arguments | Out-Host } else { & $Executable @Arguments | Out-Host }
    return $LASTEXITCODE
}
function Stop-ForAgent([int]$Code, [string]$Step) {
    switch ($Code) {
        2 { Stop-Install 2 "$Step refused the input: check the connection line or code, server address, bundle and display name." }
        3 { Stop-Install 3 "$Step could not reach the server or verify it with this code. Check the address and that Lifetime Server is running." }
        4 { Stop-Install 4 "$Step was refused by the server: the code was used, expired or revoked. Create a new code on the server." }
        default { Stop-Install 1 "$Step failed (agent exit code $Code)." }
    }
}

try {
    if ($env:OS -ne 'Windows_NT') { Stop-Install 5 'This installer requires Windows.' }
    $administrator = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Stop-Install 5 'Run this installer as Administrator (or as SYSTEM from a deployment tool).' }

    # Strict parameter checks before anything is changed. Owner consent stays explicit: reporting needs
    # -EnableReporting and exactly one way to reach the server; every optional category is off unless named.
    $sources = @(@($BundleFile, $ConnectionLine, $PairingCode) | Where-Object { $_ })
    if ($sources.Count -gt 1) { Stop-Install 2 'Use only one of -BundleFile, -ConnectionLine or -PairingCode.' }
    if ([bool]$EnableReporting -ne ($sources.Count -eq 1)) { Stop-Install 2 'Reporting requires -EnableReporting together with -ConnectionLine, -PairingCode and -Server, or -BundleFile, from the selected company server.' }
    if ([bool]$PairingCode -ne [bool]$Server) { Stop-Install 2 '-PairingCode and -Server are used together.' }
    if (($TrackHistory -or $ResourceReadings -or $HealthReadings -or $PowerControl) -and -not $EnableReporting) { Stop-Install 2 'Optional reporting categories require explicit server enrollment.' }
    if ($ConnectionLine -and ($ConnectionLine.Length -gt 2300 -or $ConnectionLine -notmatch '^\s*lifetime-connect-v1:https://[A-Za-z0-9.\-\[\]:]+ +[A-Za-z0-9\- ]{20,40}\s*$')) { Stop-Install 2 'The connection line is not valid. Copy it again from Lifetime Server (Add device).' }
    if ($PairingCode -and $PairingCode -notmatch '^\s*[A-Za-z0-9\- ]{20,40}\s*$') { Stop-Install 2 'The pairing code is not valid.' }
    if ($Server -and $Server -notmatch '^(https://)?([A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(:[0-9]{1,5})?/?$') { Stop-Install 2 'The server address is not valid. Use a name or IP address, optionally with :port.' }
    if ($BundleFile -and -not (Test-Path -LiteralPath $BundleFile -PathType Leaf)) { Stop-Install 2 'The bundle file does not exist.' }
    if ($DisplayName.Length -lt 1 -or $DisplayName.Length -gt 128 -or $DisplayName -match '[\x00-\x1F]') { Stop-Install 2 'The display name must be 1 to 128 characters without line breaks.' }
    if ($ImportLedger -and -not (Test-Path -LiteralPath $ImportLedger -PathType Leaf)) { Stop-Install 2 'The ledger to import does not exist.' }
    $secret = if ($ConnectionLine) { $ConnectionLine.Trim() } elseif ($PairingCode) { $PairingCode.Trim() } else { $null }
    $method = if ($ConnectionLine) { 'connection line' } elseif ($PairingCode) { "pairing code for $Server" } elseif ($BundleFile) { 'bundle file' } else { 'none (not reporting)' }
    Write-Log "Lifetime agent install: mode $Mode; reporting: $method; history $([bool]$TrackHistory), readings $([bool]$ResourceReadings), health $([bool]$HealthReadings), power control $([bool]$PowerControl)."

    if (Get-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -ErrorAction SilentlyContinue) { Stop-Install 6 'A Lifetime agent is already installed; nothing was changed. Uninstall it first to change its settings (data is preserved).' }
    foreach ($path in @($InstallDirectory, $DataDirectory)) {
        $absolute = [IO.Path]::GetFullPath($path).TrimEnd('\')
        foreach ($reserved in @($env:ProgramFiles, $env:ProgramData, $env:SystemRoot, (Join-Path $env:ProgramData 'Lifetime'), (Join-Path $env:SystemDrive 'Users'))) {
            if ($absolute -eq [IO.Path]::GetFullPath($reserved).TrimEnd('\')) { Stop-Install 2 'A dedicated agent directory is required; a system or shared root cannot be used.' }
        }
    }
    if (Test-Path -LiteralPath $InstallDirectory -PathType Container) {
        if ((Get-ChildItem -LiteralPath $InstallDirectory -Force | Select-Object -First 1) -and -not (Test-Path -LiteralPath (Join-Path $InstallDirectory 'lifetime_agent.py') -PathType Leaf)) { Stop-Install 2 'The existing nonempty install directory is not a Lifetime Agent installation.' }
    }
    if (Test-Path -LiteralPath $DataDirectory -PathType Container) {
        if ((Get-ChildItem -LiteralPath $DataDirectory -Force | Select-Object -First 1) -and -not (Test-Path -LiteralPath (Join-Path $DataDirectory 'machine.sqlite') -PathType Leaf)) { Stop-Install 2 'The nonempty data directory must contain an existing Lifetime machine database.' }
    }
    if ([IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\') -eq [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')) { Stop-Install 2 'Extract the package outside the target installation directory.' }

    if ($Mode -eq 'Packaged') {
        $source = Join-Path $PSScriptRoot 'LifetimeAgent.exe'
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { Stop-Install 2 'Packaged mode requires LifetimeAgent.exe.' }
        $checker = $source
        $checkerPrefix = @()
    } else {
        $command = Get-Command $Python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { Stop-Install 2 'Python was not found. Pass the full path of a machine-wide Python with -Python.' }
        $Python = $command.Source
        $userRoot = [IO.Path]::GetFullPath((Join-Path $env:SystemDrive 'Users')).TrimEnd('\') + '\'
        if ([IO.Path]::GetFullPath($Python).StartsWith($userRoot, [StringComparison]::OrdinalIgnoreCase)) { Stop-Install 2 'SYSTEM needs a machine-wide Python installation outside user profiles. Pass its full path with -Python.' }
        $checker = $Python
        $checkerPrefix = @((Join-Path $PSScriptRoot 'lifetime_agent.py'))
    }
    # The agent's own strict checks (code checksum, address, bundle, name) also run before any change.
    $enrollArguments = @()
    if ($ConnectionLine -or $PairingCode) { $enrollArguments += '--connection-stdin' }
    if ($PairingCode) { $enrollArguments += @('--server', $Server) }
    if ($BundleFile) { $enrollArguments += @('--enroll-bundle-file', [IO.Path]::GetFullPath($BundleFile)) }
    if ($EnableReporting) {
        if ($TrackHistory) { $enrollArguments += '--policy-history' }
        if ($ResourceReadings) { $enrollArguments += '--policy-resources' }
        if ($HealthReadings) { $enrollArguments += '--policy-health' }
        if ($PowerControl) { $enrollArguments += '--policy-control' }
        $enrollArguments += @('--display-name', $DisplayName)
    }
    $code = Invoke-Agent $checker (@($checkerPrefix) + @('--check') + $enrollArguments) $secret
    if ($code -ne 0) { Stop-ForAgent $code 'The parameter check' }

    $InstallDirectory = Protect-Directory $InstallDirectory
    $DataDirectory = Protect-Directory $DataDirectory
    $script:LogDirectory = $DataDirectory
    foreach ($root in @($InstallDirectory, $DataDirectory)) {
        foreach ($child in (Get-ChildItem -LiteralPath $root -Force -Recurse)) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-Install 2 'Linked installation or data child is refused.' }
            $security = if ($child.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
            $sddl = if ($child.PSIsContainer) { 'D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)' } else { 'D:P(A;;FA;;;BA)(A;;FA;;;SY)' }
            $security.SetSecurityDescriptorSddlForm($sddl)
            Set-Acl -LiteralPath $child.FullName -AclObject $security
        }
    }
    Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'lifetime_*.py' -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $InstallDirectory -Force }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall_agent_windows.ps1') -Destination $InstallDirectory -Force
    if ($Mode -eq 'Packaged') {
        Copy-Item -LiteralPath $source -Destination $InstallDirectory -Force
        $executable = Join-Path $InstallDirectory 'LifetimeAgent.exe'
        $prefixArguments = @()
        $taskPrefix = ''
    } else {
        # The machine agent uses only the standard library. No server requirements,
        # cryptography, pywin32 or Qt packages are installed in this environment.
        $environmentDir = Join-Path $InstallDirectory '.venv'
        $ErrorActionPreference = 'Continue'
        & $Python -m venv $environmentDir
        if ($LASTEXITCODE -ne 0) { Stop-Install 1 'Cannot create the dedicated machine-agent Python environment.' }
        $ErrorActionPreference = 'Stop'
        $executable = Join-Path $environmentDir 'Scripts\python.exe'
        $script = Join-Path $InstallDirectory 'lifetime_agent.py'
        $prefixArguments = @($script)
        $taskPrefix = '"' + $script + '" '
    }
    Write-Log "Installed files in $InstallDirectory; machine data in $DataDirectory."
    $arguments = @('--directory', $DataDirectory)
    if (-not (Test-Path -LiteralPath (Join-Path $DataDirectory 'machine.sqlite') -PathType Leaf)) { $arguments += '--initialize' }
    if ($ImportLedger) { $arguments += @('--import-ledger', [IO.Path]::GetFullPath($ImportLedger)) }
    if ($EnableReporting) {
        Write-Log 'Enrollment sends awake-time source totals/deltas to the server this computer verifies, after approval on the server.'
        $arguments += $enrollArguments
    }
    $code = Invoke-Agent $executable (@($prefixArguments) + $arguments) $secret
    if ($code -ne 0) { Stop-ForAgent $code 'Agent initialization, import or enrollment' }
    foreach ($value in @($InstallDirectory, $DataDirectory)) {
        if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { Stop-Install 2 'Invalid task path.' }
    }
    $taskArguments = $taskPrefix + '--directory "' + $DataDirectory + '" --run'
    $action = New-ScheduledTaskAction -Execute $executable -Argument $taskArguments -WorkingDirectory $InstallDirectory
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Lifetime machine capture and explicitly enrolled reporting' | Out-Null
    if ($Start) { Start-ScheduledTask -TaskPath '\Lifetime\' -TaskName Agent }
    Write-Log "Agent installed for SYSTEM startup. Machine data: $DataDirectory"
    Write-Log 'No inbound firewall rules were created. Per-user desktop ledgers and desktop startup registrations were not changed.'
    if ($EnableReporting) { Write-Log 'Approve this computer on Lifetime Server; it reports after approval.' }
} catch {
    if ($script:ExitCode -eq 0) { $script:ExitCode = 1 }
    Write-Log ("Failed (exit $($script:ExitCode)): " + $_.Exception.Message + ' Existing data was preserved.')
} finally {
    Save-Log
}
exit $script:ExitCode
