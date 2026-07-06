@echo off
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [WARNING] ffmpeg not found on PATH. Install from ffmpeg.org and add to PATH.
    echo           Recording will fail at Start Recording without it.
    echo.
)

if not exist venv (
    echo First run -- creating virtual environment...
    python -m venv venv
    echo Installing dependencies...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    echo.
    echo Setup done. Launching app...
) else (
    call venv\Scripts\activate.bat
)

python main.py
if errorlevel 1 (
    echo.
    echo App exited with an error -- see above.
    pause
)
