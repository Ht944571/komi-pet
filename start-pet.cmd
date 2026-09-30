@echo off
rem ============================================================
rem  start-pet.cmd - start Komi pet (via the watcher process)
rem
rem  The watcher brings the pet up within ~5s. If WorkBuddy is not
rem  running the pet stays hidden and appears once WorkBuddy opens.
rem  Double-click this file.
rem
rem  2026-09-30: prefer WorkBuddy's bundled pythonw first, because
rem  `where pythonw.exe` can resolve to another Python (anaconda etc.)
rem  that lacks the deps.
rem  NOTE: keep this file ASCII-only. cmd.exe parses .cmd with the OEM
rem  codepage, so UTF-8 punctuation here breaks the parser (verified).
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "WATCH=%ROOT%app\wb_whale_watcher.py"

set "PYW="
if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe" set "PYW=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
if not defined PYW if exist "%ROOT%.venv\Scripts\pythonw.exe" set "PYW=%ROOT%.venv\Scripts\pythonw.exe"
if not defined PYW where pythonw.exe >nul 2>nul && set "PYW=pythonw.exe"
if not defined PYW set "PYW=python.exe"

echo Starting Komi pet watcher... (%PYW%)
start "" "%PYW%" "%WATCH%"
