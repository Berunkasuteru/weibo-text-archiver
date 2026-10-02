@echo off
setlocal
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
set PYTHONUTF8=1
title Weibo Text Archiver - Environment Check

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%ROOT%\tools\build\environment_check.py"
) else (
    python "%ROOT%\tools\build\environment_check.py"
)

echo.
pause
