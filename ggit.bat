@echo off
chcp 65001 >nul
title Git一键上传工具
echo ======================================
echo          Git仓库上传工具
echo 1 - 项目第一次初始化上传
echo 2 - 日常更新推送代码
echo ======================================
echo.
echo 【第一步：选择你的项目根文件夹】
echo 示例输入:D:\下载\wechat‑ai‑reply‑main
set /p workDir=请粘贴项目完整路径: 
cd /d "%workDir%"
echo.
echo 当前工作目录 = %cd%
echo.

set /p mode=请输入功能序号(1/2): 

if "%mode%"=="1" (
    echo.
    echo ----------【初次上传流程】----------
    set /p repoUrl=请粘贴你的远程仓库HTTPS地址: 
    echo.
    echo 1.初始化本地Git仓库
    git init
    echo.
    echo 2.绑定远程仓库
    git remote add origin %repoUrl%
    echo.
    echo 3.添加当前目录全部项目文件
    git add .
    set /p msg=请填写本次提交备注: 
    echo.
    echo 4.提交本地版本
    git commit -m "%msg%"
    echo.
    echo 5.首次推送绑定master分支
    git push -u origin master
)

if "%mode%"=="2" (
    echo.
    echo ----------【代码更新推送】----------
    git status
    echo.
    echo 1.添加当前文件夹内改动文件
    git add .
    set /p msg=填写本次更新备注: 
    echo.
    echo 2.提交改动
    git commit -m "%msg%"
    echo.
    echo 3.推送至远程仓库
    git push
)

echo.
echo 操作执行完毕
pause
