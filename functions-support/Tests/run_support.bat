@echo off
:: Blueboot Support — Windows launcher
:: Run from anywhere: functions-support\Tests\run_support.bat --stats

:: cd to functions-support\ (one level up from Tests\)
cd /d "%~dp0\.."

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] No virtual environment found in functions-support\.venv or functions-support\venv
    echo Run: cd functions-support ^&^& python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    exit /b 1
)

python Tests\run_support.py %*
