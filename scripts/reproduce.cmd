@echo off
rem Runs scripts\reproduce.ps1 (see there). Arguments are passed on.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0reproduce.ps1" %*
exit /b %errorlevel%
