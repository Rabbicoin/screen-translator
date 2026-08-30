@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Возвращает файлы к выбранной версии. Текущее состояние перед этим
rem сохраняется отдельной версией, поэтому потерять ничего нельзя.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo В этой папке нет истории версий.
    pause
    exit /b 1
)

echo Последние версии:
echo.
git log -10 --pretty=format:"%%h   %%ad   %%s" --date=format:"%%d.%%m.%%Y %%H:%%M"
echo.
echo.
set "id="
set /p "id=Номер версии, к которой вернуться (пусто - отмена): "
if not defined id exit /b 0

echo.
echo Сначала сохраняю то, что есть сейчас...
git add -A
git diff --cached --quiet || git commit -q -m "Состояние перед возвратом к %id%"

git checkout %id% -- .
if errorlevel 1 (
    echo.
    echo Не получилось. Проверьте номер версии в списке выше.
    echo.
    pause
    exit /b 1
)

echo.
echo Готово: файлы вернулись к версии %id%.
echo То, что было до возврата, сохранено отдельной версией - откатить можно и его.
echo Когда убедитесь, что всё как надо, нажмите "Сохранить версию.bat".
echo.
pause
