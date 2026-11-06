@echo off
rem Double-click to install Lifetime for the current Windows user.
rem Picks the right version automatically (Python version if Smart App Control blocks unsigned programs).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows_auto.ps1"
echo.
pause
