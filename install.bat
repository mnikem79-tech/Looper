@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Установка SMK Looper

echo Установка зависимостей (нужен интернет)...
echo.

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
if not defined PY goto :nopython

echo Найден Python:
%PY% --version
echo.

%PY% -m pip install --upgrade pip
%PY% -m pip install numpy "sounddevice>=0.5.1" mido python-rtmidi

echo.
echo Проверка установки...
%PY% -c "import numpy, sounddevice, mido, rtmidi"
if errorlevel 1 goto :fail

echo.
echo ============================================
echo Готово! Закройте окно и запустите start.bat
echo ============================================
pause
exit /b 0

:nopython
echo.
echo ============================================
echo ОШИБКА: Python 3 не найден!
echo.
echo Установите Python 3 с сайта python.org:
echo   1. Откройте https://www.python.org/downloads/
echo      (страница откроется в браузере)
echo   2. Скачайте Python 3 (большая жёлтая кнопка)
echo   3. При установке ОБЯЗАТЕЛЬНО поставьте галочку
echo      "Add python.exe to PATH" внизу окна установки!
echo   4. После установки запустите install.bat снова.
echo ============================================
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:fail
echo.
echo ============================================
echo ОШИБКА: библиотеки не установились.
echo Проверьте подключение к интернету
echo и запустите install.bat снова.
echo ============================================
pause
exit /b 1
