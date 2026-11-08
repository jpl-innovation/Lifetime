# Run on a Windows PC with Python 3.10+: builds Lifetime.exe, LifetimeTracker.exe, LifetimeImport.exe and a ready-to-install "package" folder.
# Native tools (pip, PyInstaller) print progress on stderr; Windows PowerShell 5.1 would treat that as an error, so check exit codes instead.
$ErrorActionPreference = 'Continue'
python -m pip install --upgrade --disable-pip-version-check PyQt6 pyinstaller
if ($LASTEXITCODE) { throw 'pip install failed' }
$common = @('--noconfirm', '--onefile', '--icon', 'assets\lifetime.ico')
python -m PyInstaller @common --noconsole --name LifetimeTracker lifetime_tracker.py
if ($LASTEXITCODE) { throw 'PyInstaller failed (LifetimeTracker)' }
python -m PyInstaller @common --noconsole --add-data 'assets\lifetime.png;assets' --name Lifetime --collect-submodules PyQt6 lifetime_gui.py
if ($LASTEXITCODE) { throw 'PyInstaller failed (Lifetime)' }
python -m PyInstaller @common --console --name LifetimeImport --hidden-import lifetime_tracker lifetime_import_windows.py
if ($LASTEXITCODE) { throw 'PyInstaller failed (LifetimeImport)' }
python -m PyInstaller @common --console --name LifetimeAgent --exclude-module cryptography --exclude-module PyQt6 lifetime_agent.py
if ($LASTEXITCODE) { throw 'PyInstaller failed (LifetimeAgent)' }
$packageTarget = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'package'))
if ($packageTarget -ne [IO.Path]::GetFullPath((Join-Path (Get-Location).Path 'package'))) { throw 'Run this build from its repository directory.' }
Remove-Item -LiteralPath $packageTarget -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path package | Out-Null
Copy-Item dist\Lifetime.exe, dist\LifetimeTracker.exe, dist\LifetimeImport.exe, dist\LifetimeAgent.exe, install_agent_windows.ps1, uninstall_agent_windows.ps1, install_windows_exe.ps1, install_windows_common.ps1, `
    install_windows.ps1, install_windows_auto.ps1, 'Install Lifetime.cmd' package\ -Force
# The Python sources too: when Smart App Control blocks the unsigned .exe files, the installer falls back to these.
Copy-Item lifetime_*.py package\ -Force
Copy-Item README.md, TECHNICIAN_VERIFICATION.md package\ -Force
New-Item -ItemType Directory -Force -Path package\assets | Out-Null
Copy-Item assets\lifetime.png, assets\lifetime.ico package\assets\ -Force
Write-Host 'Done. Open the "package" folder and double-click "Install Lifetime.cmd".'
