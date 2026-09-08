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
echo Отправляю версии:
git log --oneline origin/main..HEAD
echo.

rem Метки версий (теги) - по ним на гитхабе делаются страницы релизов.
rem Раньше кнопка их не отправляла, и метка новой версии оставалась на
rem этом компьютере. Гитхаб тогда создавал свою - от того кода, что у него
rem уже лежал, то есть от предыдущей версии. Релиз получался с начинкой
rem от новой сборки и с исходником от старой.
for /f "delims=" %%t in ('git tag') do (
    git ls-remote --exit-code --tags origin "%%t" >nul 2>&1
    if errorlevel 1 (
        echo Новая метка версии: %%t
        set "HAS_TAGS=1"
    )
)
if defined HAS_TAGS echo.

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
git push origin --tags
if errorlevel 1 (
    echo.
    echo Версии ушли, а метки - нет. Релиз на гитхабе делать рано:
    echo без метки он привяжется не к тому коду. Покажите это окно автору.
    echo.
    pause
    exit /b 1
)

echo.
echo Готово - ушли и версии, и метки. Посмотреть:
git remote get-url origin
echo.
pause
