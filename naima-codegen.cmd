@echo off
rem naima-codegen on Windows, for a batch file to call (the twin of the sh script naima-codegen). It runs through uv, which
rem downloads the Python that pyproject.toml pins (and its libraries) on first use into the user's own folders: no
rem administrator rights and no Python on the machine are needed. The uv is, in order: %NAIMA_UV%, the one on PATH,
rem %USERPROFILE%\.local\bin\uv.exe, else uv's official standalone installer is run for this user.
rem NAIMA_PYTHON, when set, names the Python to run it with instead, and uv is not used.
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if defined PYTHONPATH (set "PYTHONPATH=%ROOT%;%PYTHONPATH%") else (set "PYTHONPATH=%ROOT%")
if defined NAIMA_PYTHON (
  "%NAIMA_PYTHON%" -m naima_codegen %*
  exit /b %errorlevel%
)
set "UV="
if defined NAIMA_UV set "UV=%NAIMA_UV%"
if not defined UV for /f "delims=" %%p in ('where uv 2^>nul') do if not defined UV set "UV=%%p"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV (
  echo naima-codegen: uv not found, installing it for this user ^(https://astral.sh/uv^) 1>&2
  set "UV_NO_MODIFY_PATH=1"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" 1>&2
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if not defined UV (
  echo naima-codegen: no uv, and it could not be installed: install uv ^(https://docs.astral.sh/uv/^) or set NAIMA_UV to it, or set NAIMA_PYTHON to a Python 3.11 or later 1>&2
  exit /b 78
)
"%UV%" run --project "%ROOT%" --no-dev --frozen --quiet python -m naima_codegen %*
exit /b %errorlevel%
