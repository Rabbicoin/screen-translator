@echo off
chcp 65001 >nul
rem Сколько раз скачали каждую версию программы с GitHub.
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
