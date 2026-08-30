@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Показывает сохранённые версии: когда, что и под каким номером.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo В этой папке нет истории версий.
    pause
    exit /b 1
)

echo Сохранённые версии, сверху самые свежие:
echo.
git log --pretty=format:"%%h   %%ad   %%s" --date=format:"%%d.%%m.%%Y %%H:%%M"
echo.
echo.
echo Слева - номер версии. Чтобы посмотреть, что в ней менялось, наберите:
echo     git show НОМЕР
echo Чтобы вернуть файлы такой версии - "Вернуть версию.bat".
echo.
pause
