@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Установка зависимостей (нужен интернет)...
py -3 -m pip install --upgrade pip
py -3 -m pip install numpy
py -3 -m pip install "sounddevice>=0.5.1"
py -3 -m pip install mido
py -3 -m pip install python-rtmidi
echo.
echo ============================================
echo Готово. Закройте окно и запустите start.bat
echo ============================================
pause
