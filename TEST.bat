@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Weibo Text Archiver - Full Test Suite

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0tests\run_tests.py"
) else (
    python "%~dp0tests\run_tests.py"
)

echo.
pause
