@echo off
cd /d "%~dp0"

echo Installing Multicam Capture like a regular Windows app...
echo.
echo Windows may ask for permission because the app will be copied to Program Files.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process PowerShell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File ""%~dp0install_shortcuts.ps1""'"
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
    echo Installer launched.
) else (
    echo Could not launch installer with error code %EXIT_CODE%.
)
echo.
pause
