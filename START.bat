@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Weibo Text Archiver - Diagnostic Console

echo.
echo ================================================
echo Weibo Text Archiver - Diagnostic Console
echo ================================================
echo Independent core - no GitHub runtime download
echo No pip install - no browser automation
echo.

where py >nul 2>nul
if %errorlevel%==0 goto use_py
where python >nul 2>nul
if %errorlevel%==0 goto use_python

echo ERROR: Python 3 was not found.
echo This release requires Python for testing.
pause
exit /b 9009

:use_py
py -3 -m weibo_archive.app
set EXITCODE=%errorlevel%
goto finished

:use_python
python -m weibo_archive.app
set EXITCODE=%errorlevel%
goto finished

:finished
if "%EXITCODE%"=="0" exit /b 0
echo.
echo Program exited with error code: %EXITCODE%
echo Detailed error may be in:
echo %%LOCALAPPDATA%%\WeiboTextExporter\last_error.txt
pause
exit /b %EXITCODE%
