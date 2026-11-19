@echo off
rem Double-click to install Lifetime Server on this Windows Server. No Python is needed.
rem It asks for administrator approval, uses this server's own network address and starts the server.
net session >nul 2>&1
if errorlevel 1 (
    set "LIFETIME_SELF=%~f0"
    powershell.exe -NoProfile -Command "Start-Process -FilePath $env:LIFETIME_SELF -Verb RunAs"
    exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_server_windows.ps1" -Start
echo.
pause
