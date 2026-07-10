@echo off
cd /d "%~dp0"

echo Starting Multicam Capture...
echo.
echo If the app closes immediately, this window will show the crash log.
echo.

"%~dp0MulticamCapture.exe"
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Multicam Capture exited with error code %EXIT_CODE%.
    set "LOG=%LOCALAPPDATA%\MulticamCapture\crash.log"
    if exist "%LOG%" (
        echo.
        echo Crash log:
        echo ------------------------------------------------------------
        type "%LOG%"
        echo ------------------------------------------------------------
    ) else (
        echo No crash log was found at "%LOG%".
    )
    echo.
    pause
)
