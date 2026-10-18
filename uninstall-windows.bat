@echo off
rem Entfernt die App wieder (Daten und MSYS2 bleiben).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" -Uninstall
pause
