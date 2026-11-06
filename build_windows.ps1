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
Remove-Item -Recurse -Force package -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path package | Out-Null
Copy-Item dist\Lifetime.exe, dist\LifetimeTracker.exe, dist\LifetimeImport.exe, install_windows_exe.ps1, install_windows_common.ps1, 'Install Lifetime.cmd' package\ -Force
Write-Host 'Done. Open the "package" folder and double-click "Install Lifetime.cmd".'
