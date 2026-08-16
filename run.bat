@echo off
REM Launcher for WiFi Direct File Transfer
set PYTHON=
for %%P in (python python3 py) do (
    %%P --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=%%P
        goto run
    )
)
echo Python not found. Please run setup.bat first.
pause
exit /b 1

:run
%PYTHON% "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo App exited with an error. Run setup.bat if you haven't already.
    pause
)
