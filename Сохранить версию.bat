@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Сохраняет текущее состояние программы как версию, к которой можно вернуться.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo В этой папке нет истории версий.
    echo Похоже, её скопировали без служебной папки .git
    echo.
    pause
    exit /b 1
)

git add -A
git diff --cached --quiet
if not errorlevel 1 (
    echo С прошлой версии ничего не изменилось - сохранять нечего.
    echo.
    pause
    exit /b 0
)

echo Что изменилось с прошлой версии:
echo.
git diff --cached --stat
echo.
set "msg="
set /p "msg=Опишите одной строкой, что сделали: "
if not defined msg set "msg=Правки без описания"
git commit -m "%msg%"
echo.
echo Версия сохранена. Весь список - "История.bat".
echo.
pause
