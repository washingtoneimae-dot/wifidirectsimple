@echo off
setlocal

rem Prefer the Windows Python launcher when it is installed and configured.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3 "%~dp0app.py"
    exit /b
  )
)

rem Then try a Python executable added to PATH.
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0app.py"
  exit /b
)

rem Finally check the normal per-user Python installation folders.
for %%V in (314 313 312 311 310) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    "%LocalAppData%\Programs\Python\Python%%V\python.exe" "%~dp0app.py"
    exit /b
  )
)

echo Python 3.10 or newer was not found.
echo Double-click setup.bat to install and check Python automatically.
pause
exit /b 1
