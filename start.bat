@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- Ищем Python 3: сначала py -3, затем python, затем python3 ---
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :found
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :found
python3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=python3"

:found
if not defined PY (
    echo Python 3 не найден! Сначала запустите install.bat - там есть инструкция.
    pause
    exit /b 1
)

%PY% looper.py
if errorlevel 1 pause
