@echo off
rem ============================================================
rem  start-pet.cmd — 启动古见同学桌宠（通过守望进程）
rem
rem  守望会在 ~5s 内把桌宠拉起来；WorkBuddy 未运行时桌宠不出现，
rem  等 WorkBuddy 打开后会自动出现。双击本文件即可。
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "WATCH=%ROOT%app\wb_whale_watcher.py"

set "PYW="
if exist "%ROOT%.venv\Scripts\pythonw.exe" set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
if not defined PYW where pythonw.exe >nul 2>nul && set "PYW=pythonw.exe"
if not defined PYW if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe" set "PYW=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
if not defined PYW set "PYW=python.exe"

echo Starting Komi pet watcher...
start "" "%PYW%" "%WATCH%"
