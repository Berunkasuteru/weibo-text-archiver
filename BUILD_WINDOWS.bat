@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "BUILD_PYTHON=.venv-build\Scripts\python.exe"

if not exist "%BUILD_PYTHON%" (
    echo Creating isolated build environment...
    python -m venv .venv-build
    if errorlevel 1 exit /b 1
)

"%BUILD_PYTHON%" -c "from importlib.metadata import version; from pathlib import Path; requirements = [line.strip() for line in Path('requirements-build.txt').read_text(encoding='utf-8').splitlines() if line.strip() and not line.startswith('#')]; assert all(version(name) == expected for name, expected in (item.split('==', 1) for item in requirements))" >nul 2>nul
if errorlevel 1 (
    echo Installing build-only dependencies...
    "%BUILD_PYTHON%" -m pip install --disable-pip-version-check --upgrade -r requirements-build.txt
    if errorlevel 1 exit /b 1
)

"%BUILD_PYTHON%" tools\package_windows_release.py --clean
if errorlevel 1 exit /b 1

"%BUILD_PYTHON%" tools\generate_icon.py
if errorlevel 1 exit /b 1

"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean weibo_text_archiver.spec
if errorlevel 1 exit /b 1

"%BUILD_PYTHON%" tools\package_windows_release.py
if errorlevel 1 exit /b 1

echo.
echo Windows preview build completed successfully.
echo Artifacts are in the release directory.
exit /b 0
