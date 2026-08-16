@echo off
setlocal
echo ============================================
echo  WiFi Direct File Transfer - Setup
echo ============================================
echo.

REM Try to find python
set PYTHON=
for %%P in (python python3 py) do (
    %%P --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=%%P
        goto found_python
    )
)

echo [ERROR] Python not found in PATH.
echo Please install Python 3.9+ from https://python.org
echo Make sure to check "Add Python to PATH" during install.
pause
exit /b 1

:found_python
echo Found: %PYTHON%
%PYTHON% --version
echo.

echo Installing required packages...
%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install winrt-Windows.Devices.WiFiDirect winrt-Windows.Security.Credentials winrt-Windows.Foundation winrt-Windows.Foundation.Collections winrt-Windows.Devices.Enumeration

if errorlevel 1 (
    echo.
    echo [WARNING] Some winrt packages failed to install.
    echo The app will still work for file transfer - just without the WiFi Direct hotspot feature.
    echo You can still use SEND/RECEIVE over any shared network.
)

echo.
echo ============================================
echo  Setup complete! Run: python main.py
echo  Or double-click: run.bat
echo ============================================
pause
