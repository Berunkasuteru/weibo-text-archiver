@echo off
setlocal
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
set PYTHONUTF8=1
title Weibo Text Archiver - Full Test Suite

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%ROOT%\tests\run_tests.py"
) else (
    python "%ROOT%\tests\run_tests.py"
)

echo.
pause
