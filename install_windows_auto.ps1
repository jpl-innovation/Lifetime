# Chooses the Windows installer that will actually run on this PC. PowerShell 5.1 compatible.
#  - Smart App Control (or App Control) blocks unsigned programs, so the .exe files cannot start: install the
#    Python version instead (Python is signed by the Python Software Foundation and is allowed).
#  - Otherwise install the ready-made .exe files.
# Native tools (pip, winget) write progress to stderr, so exit codes are checked instead of using -ErrorAction Stop.
$ErrorActionPreference = 'Continue'

function Get-SmartAppControlState {
    # 0 = off, 1 = on, 2 = evaluation mode. Missing key (older Windows) = off.
    try { return [int](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction Stop).VerifiedAndReputablePolicyState }
    catch { return 0 }
}

function Find-Python {
    foreach ($candidate in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        if ($candidate.Source -like '*\WindowsApps\*') { continue }   # the Microsoft Store stub is not a real Python
        $version = & $candidate.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]'3.10') { return $candidate.Source }
    }
    return $null
}

$state = Get-SmartAppControlState
if ($state -eq 0) {
    Write-Host 'Installing the ready-made Lifetime programs...'
    & (Join-Path $PSScriptRoot 'install_windows_exe.ps1')
    return
}

Write-Host 'Smart App Control is on, and it blocks unsigned programs such as Lifetime.exe.'
Write-Host 'Lifetime will be installed to run with Python instead, which Smart App Control allows.'

$python = Find-Python
if (-not $python) {
    Write-Host ''
    Write-Host 'Python 3.10 or newer was not found.'
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        $answer = Read-Host 'Install Python 3.12 now with winget (about 30 MB, no administrator rights needed)? (y/N)'
        if ($answer -match '^[yY]') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            Write-Host ''
            Write-Host 'Python is installed. Close this window and run "Install Lifetime.cmd" once more so Windows finds it.'
            return
        }
    }
    Write-Host 'Install Python 3.12 from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this installer again.'
    return
}
Write-Host "Using Python: $python"

& $python -c 'import PyQt6.QtWidgets' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing PyQt6 (the window toolkit) for your user...'
    & $python -m pip install --user --disable-pip-version-check PyQt6
    if ($LASTEXITCODE -ne 0) { Write-Host 'PyQt6 could not be installed. Check your internet connection and run this installer again.'; return }
}

# install_windows.ps1 starts pythonw.exe from PATH, so make sure it is this Python.
$env:PATH = (Split-Path $python) + ';' + $env:PATH
& (Join-Path $PSScriptRoot 'install_windows.ps1')
