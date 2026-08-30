@echo off
chcp 65001 >nul
rem Запуск экранного переводчика из исходников: без окна консоли, иконка
rem появится в трее. Готовой сборке (.exe) этот файл не нужен.
cd /d "%~dp0"

set "PYW="
if exist "C:\Python310\pythonw.exe" set "PYW=C:\Python310\pythonw.exe"
if not defined PYW for %%P in (pythonw.exe) do if exist "%%~$PATH:P" set "PYW=%%~$PATH:P"
if not defined PYW for %%P in (pyw.exe) do if exist "%%~$PATH:P" set "PYW=%%~$PATH:P"

if not defined PYW (
    echo Python на этом компьютере не найден.
    echo.
    echo Поставьте его с https://www.python.org/downloads/windows/
    echo (галочка "Add python.exe to PATH" при установке^), затем выполните
    echo в этой папке:  pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0screen_translator.py"
