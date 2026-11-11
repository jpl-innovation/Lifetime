# Shared source/executable installer; PowerShell 5.1 compatible.
$ErrorActionPreference = 'Stop'
Write-Host 'Administrator approval is requested to read the full Windows activity log (so Lifetime can add the time this computer was used before you installed it) and to start tracking at login.'
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Admin = ([Security.Principal.WindowsPrincipal]::new($Identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $OriginalUser) { $OriginalUser = $Identity.Name }
if (-not $OriginalLocalAppData) { $OriginalLocalAppData = $env:LOCALAPPDATA }
if (-not $OriginalAppData) { $OriginalAppData = $env:APPDATA }
if (-not $OriginalPython -and -not $ExePackage) {
    $OriginalPython = (Get-Command pythonw.exe -ErrorAction Stop).Source
}
if (-not $Admin -and -not $ElevationAttempted) {
    function Quote-PS([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
    $Invocation = '& ' + (Quote-PS $InstallerPath) + ' -ElevationAttempted' +
        ' -OriginalUser ' + (Quote-PS $OriginalUser) +
        ' -OriginalLocalAppData ' + (Quote-PS $OriginalLocalAppData) +
        ' -OriginalAppData ' + (Quote-PS $OriginalAppData) +
        ' -OriginalPython ' + (Quote-PS $OriginalPython)
    $Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Invocation))
    $Child = $null
    try {
        $Child = Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $Encoded)
    } catch {
        Write-Host 'Administrator approval was declined or unavailable. Continuing per-user; earlier usage may be incomplete.'
    }
    if ($Child) {
        if ($Child.ExitCode -ne 0) { throw "Elevated installer exited with code $($Child.ExitCode)." }
        return
    }
}
if (-not $Admin -and $ElevationAttempted) { Write-Host 'Continuing per-user; earlier usage may be incomplete.' }
$InstallDir = Join-Path $OriginalLocalAppData 'Lifetime'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if ($ExePackage) {
    $Names = @('Lifetime.exe', 'LifetimeTracker.exe', 'LifetimeImport.exe', 'LifetimeAgent.exe',
               'install_agent_windows.ps1', 'uninstall_agent_windows.ps1')
} else {
    $Names = @('lifetime_system.py', 'lifetime_system_gui.py', 'lifetime_tracker.py', 'lifetime_gui.py', 'lifetime_health.py', 'lifetime_health_gui.py', 'lifetime_shell.py', 'lifetime_signals.py', 'lifetime_dashboard.py',
               'lifetime_settings.py', 'lifetime_setup.py', 'lifetime_import.py', 'lifetime_import_windows.py',
               'lifetime_charts.py', 'lifetime_daily.py', 'lifetime_i18n.py', 'lifetime_i18n_qt.py',
               'lifetime_reports.py', 'lifetime_technician.py', 'lifetime_ledger.py', 'lifetime_identity.py', 'lifetime_theme.py',
               'lifetime_tasks.py', 'lifetime_company.py', 'lifetime_connect.py', 'lifetime_agent.py', 'lifetime_live.py', 'lifetime_reveal.py', 'lifetime_glass.py', 'lifetime_ws.py', 'lifetime_ws_client.py', 'lifetime_client.py', 'lifetime_protocol.py', 'lifetime_tls.py', 'lifetime_permissions.py', 'lifetime_connection.py', 'lifetime_discovery.py')
}
foreach ($Name in $Names) {
    $Source = Join-Path $PSScriptRoot $Name
    $Target = Join-Path $InstallDir $Name
    if ([IO.Path]::GetFullPath($Source) -ne [IO.Path]::GetFullPath($Target)) {
        Copy-Item -LiteralPath $Source -Destination $Target -Force
    }
}
if ($ExePackage) {
    $Gui = Join-Path $InstallDir 'Lifetime.exe'
    $Tracker = Join-Path $InstallDir 'LifetimeTracker.exe'
    $TaskAction = New-ScheduledTaskAction -Execute $Tracker -Argument '--run' -WorkingDirectory $InstallDir
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'assets') | Out-Null
    foreach ($Name in @('lifetime.png', 'lifetime.ico', 'jpl_innovation.png', 'jpl_innovation_light.png')) {
        $Source = Join-Path $PSScriptRoot "assets\$Name"
        $Target = Join-Path $InstallDir "assets\$Name"
        if ([IO.Path]::GetFullPath($Source) -ne [IO.Path]::GetFullPath($Target)) {
            Copy-Item -LiteralPath $Source -Destination $Target -Force
        }
    }
    $Gui = Join-Path $InstallDir 'lifetime_gui.py'
    $Tracker = Join-Path $InstallDir 'lifetime_tracker.py'
    $TaskAction = New-ScheduledTaskAction -Execute $OriginalPython -Argument "`"$Tracker`" --run" -WorkingDirectory $InstallDir
}
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $OriginalUser
$Principal = New-ScheduledTaskPrincipal -UserId $OriginalUser -LogonType Interactive -RunLevel Limited
$TaskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
try {
    Register-ScheduledTask -TaskName 'Lifetime Tracker' -Action $TaskAction -Trigger $Trigger `
        -Principal $Principal -Settings $TaskSettings -Force | Out-Null
    if ($Admin) {
        # Alternate admin credentials must not leave the original user unable
        # to enable, disable or replace their own task from Settings.
        $Sid = ([Security.Principal.NTAccount]::new($OriginalUser)).Translate([Security.Principal.SecurityIdentifier]).Value
        $Scheduler = New-Object -ComObject Schedule.Service
        $Scheduler.Connect()
        $Registered = $Scheduler.GetFolder('\').GetTask('Lifetime Tracker')
        $Registered.SetSecurityDescriptor("O:${Sid}D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;${Sid})", 0)
    }
    $Consent = $null
    $SettingsPath = Join-Path $OriginalAppData 'Lifetime\settings.json'
    if (Test-Path -LiteralPath $SettingsPath) {
        try { $Consent = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json } catch { $Consent = $null }
    }
    if ($Consent.completed -eq $true -and $Consent.autostart -eq $true) {
        Enable-ScheduledTask -TaskName 'Lifetime Tracker' | Out-Null
    } else {
        Disable-ScheduledTask -TaskName 'Lifetime Tracker' | Out-Null
    }
} catch { Write-Host 'Login task could not be registered. Lifetime will offer to configure it in Settings.' }
$Menu = Join-Path $OriginalAppData 'Microsoft\Windows\Start Menu\Programs'
New-Item -ItemType Directory -Force -Path $Menu | Out-Null
$Shell = New-Object -ComObject WScript.Shell
$ShortcutPath = Join-Path $Menu 'Lifetime.lnk'
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
if ($ExePackage) {
    $Shortcut.TargetPath = $Gui
    $Shortcut.IconLocation = $Gui + ',0'
} else {
    $Shortcut.TargetPath = $OriginalPython
    $Shortcut.Arguments = '"' + $Gui + '"'
    $Shortcut.IconLocation = Join-Path $InstallDir 'assets\lifetime.ico'
}
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Description = 'Total computer awake time'
$Shortcut.Save()
if ($Admin) {
    $Owner = [Security.Principal.NTAccount]::new($OriginalUser)
    $Items = @((Get-Item -LiteralPath $InstallDir)) + @(Get-ChildItem -LiteralPath $InstallDir -Recurse) + @((Get-Item -LiteralPath $ShortcutPath))
    foreach ($Item in $Items) {
        $Acl = Get-Acl -LiteralPath $Item.FullName
        $Acl.SetOwner($Owner)
        Set-Acl -LiteralPath $Item.FullName -AclObject $Acl
    }
}
Write-Host 'Installed for the original user. Open Lifetime from the Start menu to choose what to collect and whether to track at login.'
