@echo off
cd /d "%~dp0"

echo Installing Multicam Capture shortcuts...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_shortcuts.ps1"
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
    echo Done.
) else (
    echo Shortcut install failed with error code %EXIT_CODE%.
)
echo.
pause
