@echo off
rem ============================================================
rem  start-dashboard.cmd — 启动多 agent 用量看板服务
rem
rem  启动后浏览器打开 http://127.0.0.1:8801
rem  保留控制台窗口可以看到采集/接口日志；关掉窗口即停服。
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "API=%ROOT%app\wb_usage\wb_api.py"

set "PY="
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if not defined PY where python.exe >nul 2>nul && set "PY=python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY set "PY=python"

"%PY%" "%API%" --port 8801
