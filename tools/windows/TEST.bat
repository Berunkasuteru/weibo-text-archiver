@echo off
setlocal
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
set PYTHONUTF8=1
title Weibo Text Archiver - Full Test Suite

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    set "PY_CMD=python"
)

%PY_CMD% "%ROOT%\tests\run_tests.py" || goto :fail
%PY_CMD% "%ROOT%\tests\test_image_core.py" || goto :fail
%PY_CMD% "%ROOT%\tests\test_image_ui.py" || goto :fail

echo.
pause
exit /b 0

:fail
set "EXITCODE=%errorlevel%"
echo.
pause
exit /b %EXITCODE%
