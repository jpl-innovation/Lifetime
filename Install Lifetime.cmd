@echo off
rem Double-click to install Lifetime for the current Windows user.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows_exe.ps1"
echo.
pause
