# Removes Lifetime for this Windows user (what install_windows.ps1 / install_windows_exe.ps1 installed): closes this
# installation's programs, removes the "Lifetime Tracker" scheduled task, the sign-in tray entry, the Start menu
# shortcut, the Installed apps entry and %LOCALAPPDATA%\Lifetime. The awake-time history in %APPDATA%\Lifetime is
# kept unless -RemoveData is given. Needs no administrator approval. PowerShell 5.1 compatible.
#   powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Lifetime\uninstall_windows.ps1" [-RemoveData]
[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$Pause      # Installed apps starts it in its own window: keep the window open to read the result
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This uninstaller requires Windows.' }

function Assert-NotLinked([string]$Path) {
    # The same refusal as the installer: a junction or symbolic link would send the removal somewhere else.
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($Item -and ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Lifetime does not uninstall through a link or junction: $Path. Remove it yourself and run the uninstaller again."
    }
}
function Remove-Folder([string]$Path) {
    # Windows PowerShell's recursive delete follows a junction inside the folder and empties its target, so a folder
    # holding any link is refused before anything in it is removed.
    Assert-NotLinked $Path
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $Linked = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)
    if ($Linked.Count) {
        throw "Lifetime does not uninstall through a link or junction: $($Linked[0].FullName). Remove it yourself and run the uninstaller again."
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}
function Test-Ours([string]$Text) {
    # A command line, task action or registry value that runs something from this installation's folder.
    return [bool]$Text -and $Text.IndexOf($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase) -ge 0
}

$InstallDir = Join-Path $env:LOCALAPPDATA 'Lifetime'
$DataDir = Join-Path $env:APPDATA 'Lifetime'
try {
    Assert-NotLinked $InstallDir
    if ($RemoveData) { Assert-NotLinked $DataDir }
    # Never inside the folder being removed: PowerShell's location and the process's own working folder (Windows does
    # not delete a folder a process works in, and Installed apps or a prompt may start this script inside it).
    Set-Location -LiteralPath $env:USERPROFILE
    [Environment]::CurrentDirectory = $env:USERPROFILE

    # Close this installation's own programs (never another copy elsewhere), as the installer does before an upgrade;
    # the Python version runs as python(w).exe with this folder's scripts. The ledger is saved by atomic replacement.
    $Closed = @(Get-Process -Name Lifetime, LifetimeTracker, LifetimeImport, LifetimeAgent -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and [IO.Path]::GetDirectoryName($_.Path) -eq $InstallDir } | ForEach-Object { $_.Id })
    $Closed += @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { Test-Ours $_.CommandLine } | ForEach-Object { $_.ProcessId })
    foreach ($Id in $Closed) {
        Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $Id -Timeout 10 -ErrorAction SilentlyContinue
    }

    # The tracker's sign-in task, only when it runs this installation (another account's install may own the name).
    $Task = Get-ScheduledTask -TaskPath '\' -TaskName 'Lifetime Tracker' -ErrorAction SilentlyContinue
    if ($Task -and @($Task.Actions | Where-Object { (Test-Ours $_.Execute) -or (Test-Ours $_.Arguments) }).Count) {
        try { Unregister-ScheduledTask -TaskPath '\' -TaskName 'Lifetime Tracker' -Confirm:$false }
        catch { Write-Host 'The Lifetime Tracker task could not be removed. Remove it in Task Scheduler.' }
    }
    # The tray icon's sign-in entry (written by Lifetime when it starts automatically).
    $Run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $Value = (Get-ItemProperty -LiteralPath $Run -Name Lifetime -ErrorAction SilentlyContinue).Lifetime
    if (Test-Ours $Value) { Remove-ItemProperty -LiteralPath $Run -Name Lifetime }

    $Shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Lifetime.lnk'
    if (Test-Path -LiteralPath $Shortcut) { Assert-NotLinked $Shortcut; Remove-Item -LiteralPath $Shortcut -Force }
    $Left = $null
    try { Remove-Folder $InstallDir } catch { $Left = $_.Exception.Message }
    # Installed apps lists Lifetime as long as this uninstaller is there to be run again; once it is gone, so is the entry.
    $Uninstaller = Join-Path $InstallDir 'uninstall_windows.ps1'
    $Entry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Lifetime'
    if (-not (Test-Path -LiteralPath $Uninstaller) -and (Test-Path -LiteralPath $Entry)) { Remove-Item -LiteralPath $Entry -Recurse -Force }
    if ($Closed.Count) { Write-Host 'Lifetime was running and was closed.' }
    if (-not $Left) {
        Write-Host 'Lifetime is uninstalled for this Windows user.'
    } elseif (Test-Path -LiteralPath $Uninstaller) {
        Write-Host "Lifetime could not be removed completely ($Left). Close any window or program using $InstallDir and uninstall again."
    } else {
        Write-Host "Lifetime is uninstalled, but some files are left in $InstallDir ($Left). Delete that folder yourself."
    }

    if ($RemoveData) {
        Remove-Folder $DataDir
        Write-Host "Your Lifetime history and settings were deleted ($DataDir)."
    } elseif (Test-Path -LiteralPath $DataDir) {
        Write-Host "Your awake-time history and settings were kept in $DataDir. Installing Lifetime again continues from them; delete that folder (or run this uninstaller with -RemoveData) to remove them too."
    }
    Write-Host 'If this computer was connected to a workplace Lifetime Server, its reporting service (the machine agent) stays installed: an administrator removes it with uninstall_agent_windows.ps1 from an elevated PowerShell, for example:'
    Write-Host "  & `"$env:ProgramFiles\LifetimeAgent\uninstall_agent_windows.ps1`""
    if (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'LifetimeAgentSetup')) {
        Write-Host "The folder $env:ProgramFiles\LifetimeAgentSetup (its setup files) can then be deleted as an administrator."
    }
} finally {
    if ($Pause) { Read-Host 'Press Enter to close' | Out-Null }
}
