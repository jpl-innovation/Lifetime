# Shared hardware modules: lifetime_system.py, lifetime_system_gui.py (included by lifetime_*.py).
# Separate server build. Never changes the desktop package or its dependencies.
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$IncludeServiceWrapper
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Build this package on Windows.' }
Push-Location $PSScriptRoot
try {
    foreach ($entry in @('lifetime_server.py', 'lifetime_server_gui.py', 'lifetime_agent.py', 'requirements-server.txt', 'requirements-build.txt', 'requirements-desktop.txt')) {
        if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw "Missing build input: $entry" }
    }
    # PowerShell 5.1 treats native progress on stderr as ErrorRecords.
    $ErrorActionPreference = 'Continue'
    & $Python -m pip install --disable-pip-version-check -r requirements-server.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Server build dependencies could not be installed.' }
    $ErrorActionPreference = 'Stop'
    # A fresh output avoids stale modules from previous release bundles. Keep older
    # artifacts for rollback; no recursive deletion is used by this script.
    $tag = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $output = Join-Path $PSScriptRoot "server-package-$tag"
    $work = Join-Path $PSScriptRoot "build\server-$tag"
    New-Item -ItemType Directory -Path $output, $work -Force | Out-Null
    $icon = Join-Path $PSScriptRoot 'assets\lifetime.ico'
    $dashboardImage = (Join-Path $PSScriptRoot 'assets\lifetime.png') + ';assets'
    $serverIcon = Join-Path $PSScriptRoot 'assets\lifetime-server.ico'     # the dashboard's own (yellow) icon
    $serverLogo = (Join-Path $PSScriptRoot 'assets\lifetime-server.png') + ';assets'     # the same, for the logo in its header
    $creditImage = (Join-Path $PSScriptRoot 'assets\jpl_innovation.png') + ';assets'
    $creditLightImage = (Join-Path $PSScriptRoot 'assets\jpl_innovation_light.png') + ';assets'
    # Version information in every program (product name "Lifetime", version, company): code signing requires it.
    function New-VersionFile([string]$Program, [string]$Description) {
        $path = Join-Path $work "version-$Program.txt"
        & $Python (Join-Path $PSScriptRoot 'tools\windows_version.py') $Program $Description $path
        if ($LASTEXITCODE -ne 0) { throw "Version information failed ($Program)." }
        return $path
    }
    $common = @('--hidden-import', 'lifetime_system', '--noconfirm', '--clean', '--onefile', '--icon', $icon,
                '--distpath', $output, '--workpath', $work, '--specpath', $work)
    # Modules imported only inside functions. PyInstaller finds most of them by scanning the code, but they are named
    # here so a build never leaves one out; test_installed_modules fails when the code gains one these lists lack.
    $runtimeModules = @('lifetime_admin', 'lifetime_agent', 'lifetime_checks', 'lifetime_client', 'lifetime_connection', 'lifetime_daily',
                        'lifetime_discovery', 'lifetime_health', 'lifetime_import', 'lifetime_import_windows', 'lifetime_live',
                        'lifetime_monitor', 'lifetime_pipe', 'lifetime_reports', 'lifetime_signals', 'lifetime_system',
                        'lifetime_unix_admin', 'lifetime_updates', 'lifetime_wol', 'lifetime_ws_client', 'lifetime_ws_server') | ForEach-Object { '--hidden-import', $_ }
    $dashboardModules = @('lifetime_connection', 'lifetime_daily', 'lifetime_discovery', 'lifetime_import', 'lifetime_import_windows', 'lifetime_ledger',
                          'lifetime_pipe', 'lifetime_reports', 'lifetime_server_export', 'lifetime_server_stream',
                          'lifetime_signals', 'lifetime_tracker', 'lifetime_unix_admin') | ForEach-Object { '--hidden-import', $_ }
    $ErrorActionPreference = 'Continue'
    # zoneinfo reads IANA time zones from the tzdata package through importlib on Windows: collect its modules and data.
    & $Python -m PyInstaller @common --console --version-file (New-VersionFile LifetimeServerRuntime 'Lifetime Server') --name LifetimeServerRuntime --collect-all cryptography --collect-all tzdata @runtimeModules --exclude-module PyQt6 lifetime_server.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime build failed.' }
    & $Python -m PyInstaller @common --noconsole --version-file (New-VersionFile LifetimeServerDashboard 'Lifetime Server Dashboard') --name LifetimeServerDashboard --collect-submodules PyQt6 @dashboardModules --add-data $dashboardImage --add-data $creditImage --add-data $creditLightImage --add-data ($serverIcon + ';assets') --add-data $serverLogo --icon $serverIcon lifetime_server_gui.py
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard build failed.' }
    & $Python -m PyInstaller @common --console --version-file (New-VersionFile LifetimeAgent 'Lifetime Agent') --name LifetimeAgent --hidden-import lifetime_ws_client --exclude-module cryptography --exclude-module PyQt6 --exclude-module win32service --exclude-module win32serviceutil --exclude-module servicemanager --exclude-module pywintypes lifetime_agent.py
    if ($LASTEXITCODE -ne 0) { throw 'Agent build failed.' }
    if ($IncludeServiceWrapper) {
        & $Python -m PyInstaller @common --console --version-file (New-VersionFile LifetimeServerService 'Lifetime Server Service') --name LifetimeServerService --collect-all cryptography --collect-all tzdata @runtimeModules --hidden-import lifetime_server --exclude-module PyQt6 --hidden-import win32timezone --hidden-import win32service --hidden-import win32serviceutil --hidden-import win32event --hidden-import servicemanager --hidden-import pywintypes lifetime_server_service.py
        if ($LASTEXITCODE -ne 0) { throw 'Experimental service-wrapper build failed.' }
    }
    $ErrorActionPreference = 'Stop'
    Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'lifetime_*.py' -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $output
    }
    foreach ($name in @('install_server_windows.ps1', 'uninstall_server_windows.ps1', 'restart_server_windows.ps1',
                        'install_agent_windows.ps1', 'uninstall_agent_windows.ps1', 'requirements-server.txt',
                        'Install Lifetime Server.cmd')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $output
    }
    New-Item -ItemType Directory -Path (Join-Path $output 'assets'), (Join-Path $output 'docs') | Out-Null
    Copy-Item -LiteralPath 'assets\lifetime.png', 'assets\lifetime.ico', 'assets\lifetime-server.ico', 'assets\lifetime-server.png', 'assets\jpl_innovation.png', 'assets\jpl_innovation_light.png' -Destination (Join-Path $output 'assets')
    Copy-Item -LiteralPath 'docs\SERVER_OPERATIONS.md' -Destination (Join-Path $output 'docs')
    Copy-Item -LiteralPath 'docs\SERVER_PROTOCOL.md' -Destination (Join-Path $output 'docs')
    Copy-Item -LiteralPath 'README.md', 'TECHNICIAN_VERIFICATION.md', 'SECURITY.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md' -Destination $output
    $manifest = Get-ChildItem -LiteralPath $output -Recurse -File | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($output.Length + 1).Replace('\', '/')
        '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
    }
    [IO.File]::WriteAllLines((Join-Path $output 'SHA256SUMS.txt'), [string[]]$manifest, [Text.UTF8Encoding]::new($false))
    Write-Host "Built $output"
    Write-Host 'SCM and packaged-service installation remain unverified. See docs\SERVER_OPERATIONS.md.'
} finally {
    Pop-Location
}
