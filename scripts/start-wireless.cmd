@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-wireless.ps1" %*
exit /b %errorlevel%

