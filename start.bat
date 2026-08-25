@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 微信AI助手 - 一键启动
cd /d "%~dp0"

REM ================================================================
REM  欢迎界面
REM ================================================================
type "%~dp0banner.txt" 2>nul
echo.
echo ========================================================================
echo.
echo                    微信 AI 助手 - 一键启动
echo.
echo ========================================================================
echo.

REM ================================================================
REM  1. 检测 Python 环境
REM ================================================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] 未检测到 Python，请先安装 Python 3.10+ 并添加到系统 PATH
    echo.
    echo     下载地址: https://www.python.org/downloads/
    echo     安装时请勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python 版本: %PYVER%
echo.

REM ================================================================
REM  2. 检查/安装依赖
REM ================================================================
echo [..] 正在检查依赖是否完整...
echo.
python -c "import customtkinter, cv2, paddleocr, numpy, PIL, pyautogui, pygetwindow, pyperclip, yaml, requests, mss, win32con, psutil" >nul 2>&1
if errorlevel 1 (
    echo [!] 依赖不完整或未安装，正在自动安装...
    echo.
    echo ========================================================================
    echo   正在安装依赖，首次安装可能需要数分钟，请耐心等待...
    echo ========================================================================
    echo.
    pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [X] requirements.txt 安装失败，尝试逐个安装核心依赖...
        pip install pywin32 psutil numpy opencv-python Pillow pyautogui pygetwindow pyperclip PyYAML requests mss customtkinter paddleocr
    )
    echo.
    echo [OK] 依赖安装完成
) else (
    echo [OK] 所有依赖已安装，准备启动
)
echo.

REM ================================================================
REM  3. 启动程序 + 显示日志
REM ================================================================
echo ========================================================================
echo.
echo   正在启动微信AI助手...
echo   程序日志将实时输出到下方，请勿关闭此窗口
echo.
echo ========================================================================
echo.

python "%~dp0ui_app.py" 2>&1

REM ================================================================
REM  程序退出
REM ================================================================
echo.
echo ========================================================================
echo   程序已退出
echo   如有错误信息，请查看上方日志
echo ========================================================================
echo.
pause