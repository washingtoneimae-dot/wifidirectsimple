@echo off
:: Batch script to allow WiFi Transfer through Windows Defender Firewall
echo =======================================================
echo   Allow WiFi Transfer through Windows Firewall
echo =======================================================
echo.
echo Requesting administrator privileges...

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Administrator privileges required. Elevating...
    powershell -Command "Start-Process cmd -ArgumentList '/c %~s0' -Verb RunAs"
    exit /b
)

echo Adding inbound rule for File Transfer (TCP 5001)...
netsh advfirewall firewall delete rule name="WiFi Transfer (TCP 5001)" >nul 2>&1
netsh advfirewall firewall add rule name="WiFi Transfer (TCP 5001)" dir=in action=allow protocol=TCP localport=5001 profile=any >nul 2>&1

echo Adding inbound rule for Auto-Discovery (UDP 5002)...
netsh advfirewall firewall delete rule name="WiFi Transfer (UDP 5002)" >nul 2>&1
netsh advfirewall firewall add rule name="WiFi Transfer (UDP 5002)" dir=in action=allow protocol=UDP localport=5002 profile=any >nul 2>&1

echo.
echo [SUCCESS] Windows Firewall rules added successfully!
echo Ports TCP 5001 and UDP 5002 are now allowed on all networks.
echo.
pause
