@echo off
setlocal
cd /d "%~dp0\.."
set PYTHONUTF8=1
title Weibo Archive - Tests

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0run_tests.py"
) else (
    python "%~dp0run_tests.py"
)

echo.
pause
