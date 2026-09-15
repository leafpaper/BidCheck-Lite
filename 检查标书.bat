@echo off
chcp 65001 >nul
setlocal
title 标书检查 Lite
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] 找不到 .venv，请先在本目录执行 uv sync 或参考 README 创建虚拟环境。
  pause
  exit /b 1
)
cd /d "%ROOT%"
set "PYTHONPATH=%ROOT%"
set "PYTHONIOENCODING=utf-8"
echo 正在启动标书检查（浏览器会自动打开 http://127.0.0.1:8765 ）
echo 关闭本窗口即退出。
"%PY%" -m services.fujian_check.lite.server
pause
endlocal
