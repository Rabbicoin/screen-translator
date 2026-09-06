@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Отправляет сохранённые версии на GitHub. Сначала «Сохранить версию.bat»,
rem потом этот — уйдёт всё, что накопилось с прошлой отправки.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo В этой папке нет истории версий.
    echo.
    pause
    exit /b 1
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo Не задан адрес репозитория на GitHub.
    echo.
    pause
    exit /b 1
)

echo Проверяю, что уже на гитхабе...
git fetch origin >nul 2>&1

git diff --quiet
if errorlevel 1 (
    echo.
    echo ВНИМАНИЕ: есть несохранённые правки - они НЕ уйдут.
    echo Сначала нажмите "Сохранить версию.bat".
    echo.
)

echo.
echo Отправляю:
git log --oneline origin/main..HEAD
echo.

git push origin main
if errorlevel 1 (
    echo.
    echo Не отправилось. Обычно это вход в GitHub: логин и пароль
    echo спрашиваются в отдельном окне, пароль - не от сайта, а токен.
    echo.
    pause
    exit /b 1
)

echo.
echo Готово. Посмотреть:
git remote get-url origin
echo.
pause
