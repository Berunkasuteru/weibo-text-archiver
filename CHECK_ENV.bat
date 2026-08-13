@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Weibo Text Archiver - Environment Check

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0environment_check.py"
) else (
    python "%~dp0environment_check.py"
)

echo.
pause
