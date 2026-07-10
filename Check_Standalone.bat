@echo off
cd /d "%~dp0"

echo Checking Multicam Capture standalone files...
echo This checks Python libraries, camera/screen modules, and bundled ffmpeg.
echo.

"%~dp0MulticamCapture.exe" --self-test
set EXIT_CODE=%ERRORLEVEL%

if "%EXIT_CODE%"=="0" (
    echo.
    echo Standalone check passed. This folder should run without internet.
) else (
    echo.
    echo Standalone check failed with error code %EXIT_CODE%.
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
)

echo.
pause
