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
REM  NOTE2: Tsinghua mirror for CN users (10~50x faster than default pypi.org).
REM ================================================================
:CHECK_DEPS
echo [..] Checking dependencies...
echo.
set "PIP_MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn"
REM  ----------------------------------------------------------------
REM  ★ 依赖完整性检查（AST 扫描所有 .py import 后生成的完整列表）
REM     缺任何一个就会跳清华源自动装，不再发生"换电脑缺一堆"
REM  ----------------------------------------------------------------
REM  核心必需:
REM    paddleocr + paddle       OCR 壳 + paddlepaddle 推理后端（缺一就 0 条）
REM    customtkinter            UI
REM    cv2, PIL                 图像处理/裁剪
REM    numpy, mss               数值 / 截图
REM    pyautogui, pygetwindow,  窗口/鼠标/剪贴板操作(Windows)
REM    pyperclip, win32con,
REM    psutil
REM    yaml (PyYAML)            配置
REM    requests                 LLM API 调用
REM    matplotlib               面板统计图表（不装每10秒报错）
REM  次必需:
REM    comtypes                 pywin32/uiautomation 的 COM 底层
REM    onnxruntime              红点 CNN 模型推理
REM    sklearn (scikit-learn)   TfidfVectorizer 语义去重
REM    sentence_transformers    RAG 向量化
REM    chromadb                 RAG 向量库
REM    uiautomation             另一种后台点击注入方案
REM  ----------------------------------------------------------------
python -c "import customtkinter,cv2,paddleocr,paddle,numpy,PIL,pyautogui,pygetwindow,pyperclip,yaml,requests,mss,win32con,psutil,matplotlib,comtypes,onnxruntime,sklearn,sentence_transformers,chromadb,uiautomation" >nul 2>&1
if not errorlevel 1 goto DEPS_OK

echo [!] Missing dependencies, installing via Tsinghua mirror...
echo.
echo ========================================================================
echo   Installing dependencies (first run may take several minutes)...
echo ========================================================================
echo.
python -m pip install --upgrade pip %PIP_MIRROR%
python -m pip install -r "%~dp0requirements.txt" %PIP_MIRROR%
if not errorlevel 1 goto LAUNCH

echo.
echo [X] requirements.txt failed, trying fallback install...
python -m pip install pywin32 psutil numpy opencv-python Pillow pyautogui pygetwindow pyperclip PyYAML requests mss customtkinter paddleocr paddlepaddle matplotlib comtypes onnxruntime scikit-learn sentence-transformers chromadb uiautomation %PIP_MIRROR%
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