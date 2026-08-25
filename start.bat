@echo off
title 微信AI助手 - 一键启动
cd /d "%~dp0"

echo.
echo ============================================================
echo            微信AI助手 - 一键启动 (One-Click Launcher)
echo ============================================================
echo.

REM ============================================================
REM  1. 检查 Python
REM ============================================================
python --version >nul 2>&1
if errorlevel 1 goto NO_PYTHON
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python: %PYVER%
echo.
goto CHECK_CONFIG

:NO_PYTHON
echo [X] 未检测到 Python！请安装 Python 3.10+ 并务必勾选「Add Python to PATH」
echo     下载地址: https://www.python.org/downloads/
echo.
pause
exit /b 1

REM ============================================================
REM  2. 首次运行自动生成 config.yaml（从模板复制，已存在则跳过）
REM     config.yaml 因含 API Key 被 gitignore，不会进 GitHub，
REM     所以别人 clone/下载后首次运行必须由本步或程序自动生成。
REM ============================================================
:CHECK_CONFIG
if exist config.yaml (
    echo [OK] 配置文件已存在: config.yaml
) else (
    if exist config.example.yaml (
        copy /Y config.example.yaml config.yaml >nul
        echo [OK] 已根据模板自动生成 config.yaml
        echo [提示] 自动回复功能需在 config.yaml 中填入你的 LLM API Key 后才能使用
    ) else (
        echo [X] 未找到 config.example.yaml，无法自动生成配置，请重新下载完整仓库
    )
)
echo.

REM ============================================================
REM  3. 配置国内最佳镜像源 + 检查/安装依赖
REM     镜像优先级（paddle/paddleocr 体积大，必须走国内镜像）:
REM       1) 阿里云  https://mirrors.aliyun.com/pypi/simple/   企业级CDN，最快最稳
REM       2) 腾讯云  https://mirrors.cloud.tencent.com/pypi/simple/
REM       3) 清华    https://pypi.tuna.tsinghua.edu.cn/simple
REM     先把阿里云写进用户 pip.ini，后续所有 pip install 全局继承，
REM     不必每条命令拼 -i 参数（老写法易回落到国外 pypi.org）。
REM ============================================================
:CHECK_DEPS
echo [..] 正在检查依赖...
set "PIP_MIRROR=-i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com"
set "PIP_MIRROR2=-i https://mirrors.cloud.tencent.com/pypi/simple/ --trusted-host mirrors.cloud.tencent.com"
set "PIP_MIRROR3=-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn"

python -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ >nul 2>&1
python -m pip config set global.trusted-host mirrors.aliyun.com >nul 2>&1
python -m pip config set global.timeout 120 >nul 2>&1
echo [OK] 已配置国内镜像源 (阿里云优先)

python -c "import customtkinter,cv2,paddleocr,paddle,numpy,PIL,pyautogui,pygetwindow,pyperclip,yaml,requests,mss,win32con,psutil,matplotlib,comtypes,onnxruntime,sklearn,uiautomation" >nul 2>&1
if not errorlevel 1 goto DEPS_OK

echo [!] 缺少依赖，正在通过阿里云镜像安装（国内最快，首次可能需几分钟）...
python -m pip install -r "%~dp0requirements.txt" %PIP_MIRROR%
if not errorlevel 1 goto LAUNCH
echo [X] 阿里云失败，尝试腾讯云镜像...
python -m pip install -r "%~dp0requirements.txt" %PIP_MIRROR2%
if not errorlevel 1 goto LAUNCH
echo [X] 腾讯云失败，尝试清华镜像...
python -m pip install -r "%~dp0requirements.txt" %PIP_MIRROR3%
if not errorlevel 1 goto LAUNCH
echo [X] 三个镜像均失败，请检查网络后重新运行 start.bat
echo.
pause
exit /b 1

:DEPS_OK
echo [OK] 依赖已就绪
echo.

REM ============================================================
REM  4. 启动程序
REM ============================================================
:LAUNCH
echo ============================================================
echo   正在启动微信AI助手...（日志见下方，请勿关闭此窗口）
echo ============================================================
echo.

python "%~dp0ui_app.py" 2>&1

echo.
echo ============================================================
echo   程序已退出，详见上方日志。
echo ============================================================
echo.
pause
