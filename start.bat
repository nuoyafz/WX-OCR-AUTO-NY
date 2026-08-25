@echo off
title WX AI Assistant - Launcher
cd /d "%~dp0"

REM ================================================================
REM  WX AI Assistant - One-Click Launcher
REM ================================================================
type "%~dp0banner.txt" 2>nul
echo.
echo ========================================================================
echo.
echo              WX AI Assistant - One-Click Launcher
echo.
echo ========================================================================
echo.

REM ================================================================
REM  1. Check Python
REM ================================================================
python --version >nul 2>&1
if errorlevel 1 goto NO_PYTHON
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python: %PYVER%
echo.
goto CHECK_DEPS

:NO_PYTHON
echo [X] Python not found! Please install Python 3.10+ and add to PATH
echo.
echo     Download: https://www.python.org/downloads/
echo     Check "Add Python to PATH" during installation
echo.
pause
exit /b 1

REM ================================================================
REM  2. Check / Install Dependencies
REM  NOTE: Use "python -m pip" instead of "pip.exe" directly.
REM        Some Python launchers (workbuddy/uv etc.) have a broken pip.exe
REM        shim where the hardcoded python path loses backslashes, causing
REM        "Fatal error in launcher: Unable to create process".
REM        python -m pip bypasses the shim and always uses the same interpreter.
REM ================================================================
:CHECK_DEPS
echo [..] Checking dependencies...
echo.
python -c "import customtkinter, cv2, paddleocr, numpy, PIL, pyautogui, pygetwindow, pyperclip, yaml, requests, mss, win32con, psutil" >nul 2>&1
if not errorlevel 1 goto DEPS_OK

echo [!] Missing dependencies, installing...
echo.
echo ========================================================================
echo   Installing dependencies (first run may take several minutes)...
echo ========================================================================
echo.
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
if not errorlevel 1 goto LAUNCH

echo.
echo [X] requirements.txt failed, trying fallback install...
python -m pip install pywin32 psutil numpy opencv-python Pillow pyautogui pygetwindow pyperclip PyYAML requests mss customtkinter paddleocr paddlepaddle
echo.
echo [OK] Dependencies installed
goto LAUNCH

:DEPS_OK
echo [OK] All dependencies ready
echo.

REM ================================================================
REM  3. Launch App
REM ================================================================
:LAUNCH
echo ========================================================================
echo.
echo   Launching WX AI Assistant...
echo   Logs will appear below. Do NOT close this window.
echo.
echo ========================================================================
echo.

python "%~dp0ui_app.py" 2>&1

REM ================================================================
REM  Exit
REM ================================================================
echo.
echo ========================================================================
echo   App exited. Check logs above for errors.
echo ========================================================================
echo.
pause