@echo off
rem naima-codegen on Windows, for a batch file to call: it starts the naima-codegen script with Python, the one named
rem by NAIMA_PYTHON (the Python naima tools install python installs) or else the python on PATH.
setlocal
if defined NAIMA_PYTHON (set "py=%NAIMA_PYTHON%") else (set "py=python")
"%py%" "%~dp0naima-codegen" %*
exit /b %errorlevel%
