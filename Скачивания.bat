@echo off
chcp 65001 >nul
rem Сколько раз скачали каждую версию программы с GitHub: табличка здесь
rem и график в браузере — даты выхода версий и скачивания по дням.
rem Каждый запуск записывает замер, из них и складываются дни.
rem Проверочные скачивания при выпуске версий не считаются.
cd /d "%~dp0"

set "PY="
if exist "C:\Python310\python.exe" set "PY=C:\Python310\python.exe"
if not defined PY for %%P in (python.exe) do if exist "%%~$PATH:P" set "PY=%%~$PATH:P"

if not defined PY (
    echo Python не найден — посчитать нечем.
    pause
    exit /b 1
)

"%PY%" -u "%~dp0downloads.py"
echo.
pause
