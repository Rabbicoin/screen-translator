@echo off
chcp 65001 >nul
rem Собирает готовую программу для чужого компьютера: папку dist\ScreenTranslator
rem и архив рядом с ней. Нужен Python с PyInstaller — см. README, раздел «Сборка».
cd /d "%~dp0"

set "PY="
if exist "C:\Python310\python.exe" set "PY=C:\Python310\python.exe"
if not defined PY for %%P in (python.exe) do if exist "%%~$PATH:P" set "PY=%%~$PATH:P"

if not defined PY (
    echo Python не найден — собирать нечем.
    pause
    exit /b 1
)

"%PY%" -u "%~dp0build.py" %*
echo.
pause
