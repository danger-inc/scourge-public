@echo off
rem Installiert die App unter Windows (siehe install-windows.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
pause
