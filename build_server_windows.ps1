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
    foreach ($entry in @('lifetime_server.py', 'lifetime_server_gui.py', 'lifetime_agent.py', 'requirements-server.txt')) {
        if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw "Missing build input: $entry" }
    }
    # PowerShell 5.1 treats native progress on stderr as ErrorRecords.
    $ErrorActionPreference = 'Continue'
    & $Python -m pip install --disable-pip-version-check -r requirements-server.txt PyQt6 pyinstaller
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
    $common = @('--noconfirm', '--clean', '--onefile', '--icon', $icon,
                '--distpath', $output, '--workpath', $work, '--specpath', $work)
    $ErrorActionPreference = 'Continue'
    & $Python -m PyInstaller @common --console --name LifetimeServerRuntime --collect-all cryptography --exclude-module PyQt6 lifetime_server.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime build failed.' }
    & $Python -m PyInstaller @common --noconsole --name LifetimeServerDashboard --collect-submodules PyQt6 --add-data $dashboardImage lifetime_server_gui.py
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard build failed.' }
    & $Python -m PyInstaller @common --console --name LifetimeAgent --exclude-module cryptography --exclude-module PyQt6 --exclude-module win32service --exclude-module win32serviceutil --exclude-module servicemanager --exclude-module pywintypes lifetime_agent.py
    if ($LASTEXITCODE -ne 0) { throw 'Agent build failed.' }
    if ($IncludeServiceWrapper) {
        & $Python -m PyInstaller @common --console --name LifetimeServerService --collect-all cryptography --hidden-import win32timezone --hidden-import win32service --hidden-import win32serviceutil --hidden-import win32event --hidden-import servicemanager --hidden-import pywintypes lifetime_server_service.py
        if ($LASTEXITCODE -ne 0) { throw 'Experimental service-wrapper build failed.' }
    }
    $ErrorActionPreference = 'Stop'
    Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'lifetime_*.py' -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $output
    }
    foreach ($name in @('install_server_windows.ps1', 'uninstall_server_windows.ps1',
                        'install_agent_windows.ps1', 'uninstall_agent_windows.ps1', 'requirements-server.txt')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $output
    }
    New-Item -ItemType Directory -Path (Join-Path $output 'assets'), (Join-Path $output 'docs') | Out-Null
    Copy-Item -LiteralPath 'assets\lifetime.png', 'assets\lifetime.ico' -Destination (Join-Path $output 'assets')
    Copy-Item -LiteralPath 'docs\SERVER_OPERATIONS.md' -Destination (Join-Path $output 'docs')
    Copy-Item -LiteralPath 'docs\SERVER_PROTOCOL.md' -Destination (Join-Path $output 'docs')
    Copy-Item -LiteralPath 'README.md', 'TECHNICIAN_VERIFICATION.md' -Destination $output
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
