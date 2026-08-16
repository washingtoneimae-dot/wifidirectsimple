@echo off
setlocal EnableExtensions
title PeerDrop LAN Setup

echo.
echo =====================================
echo          PeerDrop LAN Setup
echo =====================================
echo.

call :find_python
if defined PYTHON_EXE goto :verify

echo Python 3.10 or newer was not found.
echo This setup will install Python 3.13 for the current PC.
echo.
where winget >nul 2>nul
if errorlevel 1 (
  echo Windows Package Manager ^(winget^) is unavailable on this PC.
  echo Install Python from https://www.python.org/downloads/windows/
  echo Then run this setup again.
  pause
  exit /b 1
)

set /p INSTALL="Install Python 3.13 now? [Y/n]: "
if /I "%INSTALL%"=="n" (
  echo Setup cancelled.
  exit /b 0
)

winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo.
  echo Python installation did not complete. Check the message above and try again.
  pause
  exit /b 1
)

call :find_python
if not defined PYTHON_EXE (
  echo.
  echo Python was installed, but Windows has not made it available yet.
  echo Close this window, open setup.bat again, then start run.bat.
  pause
  exit /b 0
)

:verify
echo.
echo Using: %PYTHON_EXE%
"%PYTHON_EXE%" --version
"%PYTHON_EXE%" -c "import tkinter; print('Desktop UI support: OK')"
if errorlevel 1 (
  echo The installed Python is missing Tkinter, which this app needs for its window.
  pause
  exit /b 1
)

echo.
echo Running the PeerDrop checks...
"%PYTHON_EXE%" -m unittest -v
if errorlevel 1 (
  echo.
  echo Setup completed, but the local checks did not pass.
  pause
  exit /b 1
)

echo.
echo Setup complete. You can now double-click run.bat to start PeerDrop LAN.
pause
exit /b 0

:find_python
set "PYTHON_EXE="
for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE exit /b 0
for %%V in (314 313 312 311 310) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python%%V\python.exe"
)
exit /b 0
