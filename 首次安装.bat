@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 创作者工作台 - 首次安装
echo.
echo  创作者工作台首次安装
echo  ====================
echo  安装只需要做一次，文件会放在当前工作台目录。
echo.
where py >nul 2>&1
if errorlevel 1 (
  echo [需要处理] 电脑中没有 Python。
  echo 请先从 https://www.python.org/downloads/windows/ 安装 Python 3.11 或更高版本，
  echo 安装时勾选 Add Python to PATH，然后再次双击本文件。
  pause
  exit /b 1
)
echo [1/4] 创建本地运行环境...
py -3 -m venv runtime
if errorlevel 1 goto failed
echo [2/4] 安装工作台依赖...
runtime\Scripts\python.exe -m pip install --upgrade pip
runtime\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo [3/4] 安装抖音同步所需浏览器组件...
runtime\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto failed
echo [4/4] 检查完成。
echo.
echo 安装成功！以后只需要双击“打开工作台.vbs”。
pause
exit /b 0
:failed
echo.
echo 安装没有完成。请检查网络后重试；已有数据不会被删除。
pause
exit /b 1
