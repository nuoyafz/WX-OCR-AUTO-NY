@echo off
chcp 65001 >nul
echo ============================================================
echo             微信AI助手 - 功能修复工具
echo ============================================================
echo.

:menu
echo 请选择操作:
echo   1. 一键修复所有功能
echo   2. 运行功能诊断
echo   3. 修复配置文件
echo   4. 验证API Key
echo   5. 查看使用说明
echo   0. 退出
echo.

set /p choice="请输入选项 (0-5): "

if "%choice%"=="1" goto fix_all
if "%choice%"=="2" goto diagnose
if "%choice%"=="3" goto fix_config
if "%choice%"=="4" goto validate_api
if "%choice%"=="5" goto show_help
if "%choice%"=="0" goto end
echo 无效选项，请重新选择
goto menu

:fix_all
echo.
echo [1/2] 开始应用功能修复...
python fix_integration.py --fix
if %errorlevel% neq 0 (
    echo ❌ 功能修复失败
    pause
    goto menu
)
echo.
echo [2/2] 开始修复配置文件...
python config_fixer.py
echo.
echo ✅ 修复完成！建议重启程序测试功能
pause
goto menu

:diagnose
echo.
echo 运行功能诊断...
python fix_integration.py --diagnose
pause
goto menu

:fix_config
echo.
echo 修复配置文件...
python config_fixer.py
pause
goto menu

:validate_api
echo.
echo 验证API Key...
python config_fixer.py --validate-only
pause
goto menu

:show_help
echo.
echo ============================================================
echo                    使用说明
echo ============================================================
echo.
echo 常见问题解决:
echo.
echo 1. 自动回复不工作
echo    - 运行: 选项1 (一键修复)
echo    - 检查: config.yaml 中的 API Key 是否有效
echo    - 获取API Key: https://dashscope.aliyuncs.com/
echo.
echo 2. 数据统计不更新
echo    - 运行: 选项1 (一键修复)
echo    - 在UI中点击"刷新统计"按钮
echo    - 检查数据库文件: data/messages.db
echo.
echo 3. API Key无效
echo    - 运行: 选项4 (验证API Key)
echo    - 更新config.yaml中的api_key字段
echo    - 重新运行: 选项1 (一键修复)
echo.
echo 4. 程序崩溃或异常
echo    - 查看日志: wechat_ai_reply.log
echo    - 运行诊断: 选项2 (功能诊断)
echo    - 重置配置: 删除config.yaml，重新运行程序
echo.
echo 详细文档: FIXES_README.md
echo.
pause
goto menu

:end
echo.
echo 感谢使用！
pause