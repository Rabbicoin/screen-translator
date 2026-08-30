@echo off
chcp 65001 >nul
rem То же самое, но с окном консоли и подробным логом — если что-то не работает.
cd /d "%~dp0"
set ST_DEBUG=1

set "PY="
if exist "C:\Python310\python.exe" set "PY=C:\Python310\python.exe"
if not defined PY for %%P in (python.exe) do if exist "%%~$PATH:P" set "PY=%%~$PATH:P"
if not defined PY for %%P in (py.exe) do if exist "%%~$PATH:P" set "PY=%%~$PATH:P"

if not defined PY (
    echo Python на этом компьютере не найден.
    echo Поставьте его с https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

"%PY%" -u "%~dp0screen_translator.py"
pause
