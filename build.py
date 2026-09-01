# -*- coding: utf-8 -*-
"""Сборка готовой программы для чужого компьютера.

Что делает:
  1. рисует иконку `icon.ico`;
  2. собирает PyInstaller-ом два .exe (обычный и отладочный) в `dist`;
  3. кладёт рядом Tesseract и языки распознавания — на чужом компьютере их нет;
  4. добавляет словарь, переводы интерфейса, документацию;
  5. упаковывает всё в один .zip, который и отдают человеку.

Запуск:  Сборка.bat   (или  python build.py)
Ключи:   --no-zip     — не тратить время на архив (пересборка «на посмотреть»)
         --keep-langs eng,rus,deu  — взять только эти языки, чтобы архив похудел

Папку сборки нельзя переименовывать в русскую: Tesseract не открывает пути с
кириллицей, а языки лежат внутри неё. Отсюда латинское имя ScreenTranslator и
русские имена только у самих .exe — на их путь Tesseract не смотрит.
"""

import argparse
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
import time

# Сообщения тут русские, а консоль Windows по умолчанию не в UTF-8 — без этого
# скрипт падает на собственном сообщении об ошибке. `Сборка.bat` делает chcp.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
BUNDLE = os.path.join(DIST, "ScreenTranslator")       # что уедет к человеку
STAGE = os.path.join(DIST, "_stage")                   # куда складывает PyInstaller
ICON = os.path.join(HERE, "icon.ico")

EXE_MAIN = "Переводчик.exe"
EXE_DEBUG = "Переводчик (отладка).exe"

# Файлы, которые человек может открыть и поправить: лежат рядом с .exe.
# README едет отдельно и урезанным — см. public_readme.
SHIPPED_FILES = ["ocr_words.txt"]
# Словарь замен у каждого свой, и в нём легко оказываются рабочие термины автора.
# В сборку едет образец, а не рабочий файл: имя слева — что берём, справа — как ляжет.
SHIPPED_AS = {"glossary.example.json": "glossary.json"}
SHIPPED_GLOBS = ["ui_*.json"]

# Разделы README, которые к человеку не едут. «Как устроено» — разбор того, как
# программа режет текст на абзацы и подбирает кегль; это самое дорогое, что в
# ней есть, и единственное, что нельзя восстановить из готового .exe за вечер.
# Остальные три ему просто не нужны: он не собирает и не рекламирует.
PRIVATE_SECTIONS = [
    "### Строка внизу окна: контакты и объявления",
    "## Запуск из исходников",
    "## Сборка .exe",
    "## Как устроено",
]

# Откуда брать Tesseract и языки. Первый подходящий путь и берём.
TESSERACT_DIRS = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR"),
]
TESSDATA_DIRS = [
    os.path.join(os.environ.get("USERPROFILE", ""), "ScreenTranslator", "tessdata"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ScreenTranslator", "tessdata"),
]

# Без этих двух программа не работает вовсе: eng — запасной язык распознавания,
# osd — определение письменности, на нём держится режим "auto".
REQUIRED_LANGS = ["eng", "osd"]


def say(text):
    print(text, flush=True)


def die(text):
    print("\nОШИБКА: " + text, flush=True)
    sys.exit(1)


def locked_file(folder):
    """Первый файл, занятый другой программой, или None.

    Проверять надо ДО того, как что-то удалено. Иначе выходит так: очистка
    сносит половину папки, спотыкается о запущенную программу и умирает — а у
    человека остаётся сборка без Tesseract и без настроек, то есть сломанная
    молча и наполовину. Держат файлы только исполняемые: .exe, .dll, .pyd.
    """
    for root, _, files in os.walk(folder):
        for name in files:
            if not name.lower().endswith((".exe", ".dll", ".pyd")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r+b"):
                    pass
            except OSError:
                return path
    return None


def first_existing(paths, what):
    for path in paths:
        if path and os.path.isdir(path):
            return path
    die(f"не нашёл {what}. Искал:\n  " + "\n  ".join(p for p in paths if p))


# --------------------------------------------------------------------------------------
#  Иконка
# --------------------------------------------------------------------------------------
def make_icon():
    """Синий квадрат с белой «Я» — та же, что программа рисует себе в трее."""
    from PIL import Image, ImageDraw, ImageFont

    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((0, 0, 255, 255), radius=48, fill="#1a73e8")
    font = None
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        try:
            font = ImageFont.truetype(name, 150)
            break
        except OSError:
            continue
    draw.text((128, 132), "Я", font=font or ImageFont.load_default(),
              fill="white", anchor="mm")
    base.save(ICON, sizes=[(s, s) for s in sizes])
    say(f"иконка: {ICON}")


# --------------------------------------------------------------------------------------
#  PyInstaller
# --------------------------------------------------------------------------------------
def run_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        die("не установлен PyInstaller. Поставьте его командой:\n"
            f'  "{sys.executable}" -m pip install pyinstaller')

    say("собираю .exe (первый раз это пара минут)…")
    # PyInstaller сносит папку назначения целиком, а её может держать открытое
    # окно проводника: удалить каталог Windows тогда не даёт (WinError 32), хотя
    # писать внутрь позволяет. Поэтому собираем в свою временную папку и
    # раскладываем оттуда сами — сборка перестаёт зависеть от того, куда человек
    # заглянул мышкой.
    shutil.rmtree(STAGE, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--distpath", STAGE, os.path.join(HERE, "screen_translator.spec")]
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        die("PyInstaller не справился — смотрите его сообщения выше.")

    staged = os.path.join(STAGE, "ScreenTranslator")
    busy = locked_file(BUNDLE) if os.path.isdir(BUNDLE) else None
    if busy:
        die("занят файл " + os.path.relpath(busy, BUNDLE) + ".\n"
            "Похоже, программа из dist\\ScreenTranslator сейчас запущена.\n"
            "Выйдите из неё через иконку в трее и запустите сборку снова.\n"
            "Ничего не удалено, прошлая сборка цела.")
    if os.path.isdir(BUNDLE):
        for name in os.listdir(BUNDLE):
            path = os.path.join(BUNDLE, name)
            try:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            except OSError as e:
                die(f"не убрать прошлую сборку: {e}\n"
                    "Закройте программу из dist\\ScreenTranslator, если она запущена.")
    os.makedirs(BUNDLE, exist_ok=True)
    for name in os.listdir(staged):
        shutil.move(os.path.join(staged, name), os.path.join(BUNDLE, name))
    shutil.rmtree(STAGE, ignore_errors=True)

    for src, dst in (("ScreenTranslator.exe", EXE_MAIN),
                     ("ScreenTranslator-debug.exe", EXE_DEBUG)):
        src_path = os.path.join(BUNDLE, src)
        if not os.path.isfile(src_path):
            die(f"после сборки нет файла {src}")
        shutil.move(src_path, os.path.join(BUNDLE, dst))
    say(f"собрано: {EXE_MAIN} и {EXE_DEBUG}")


# --------------------------------------------------------------------------------------
#  Tesseract
# --------------------------------------------------------------------------------------
def copy_tesseract():
    """Кладём в комплект сам tesseract.exe и его библиотеки.

    Остальные .exe из установки — инструменты обучения, документация и языки
    оттуда же нам не нужны: языки берём отдельно, свежие и в нужном наборе.
    """
    src = first_existing(TESSERACT_DIRS, "установленный Tesseract")
    dst = os.path.join(BUNDLE, "tesseract")
    os.makedirs(dst, exist_ok=True)

    copied = 0
    for name in os.listdir(src):
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            continue
        low = name.lower()
        if low == "tesseract.exe" or low.endswith(".dll") or low == "license":
            shutil.copy2(path, os.path.join(dst, name))
            copied += 1
    if not os.path.isfile(os.path.join(dst, "tesseract.exe")):
        die(f"в {src} нет tesseract.exe")
    say(f"Tesseract: {copied} файлов из {src}")
    return dst


def copy_tessdata(tesseract_dir, keep=None):
    """Языки распознавания. `keep` — список кодов, если нужен набор поменьше."""
    src = None
    for path in TESSDATA_DIRS + [os.path.join(d, "tessdata") for d in TESSERACT_DIRS]:
        if path and glob.glob(os.path.join(path, "*.traineddata")):
            src = path
            break
    if not src:
        die("не нашёл ни одного файла *.traineddata — языки распознавания.\n"
            "Обычно они лежат в %USERPROFILE%\\ScreenTranslator\\tessdata")

    dst = os.path.join(tesseract_dir, "tessdata")
    os.makedirs(dst, exist_ok=True)
    names, total = [], 0
    for path in sorted(glob.glob(os.path.join(src, "*.traineddata"))):
        code = os.path.basename(path)[:-len(".traineddata")]
        if keep and code not in keep and code not in REQUIRED_LANGS:
            continue
        shutil.copy2(path, os.path.join(dst, os.path.basename(path)))
        names.append(code)
        total += os.path.getsize(path)

    missing = [c for c in REQUIRED_LANGS if c not in names]
    if missing:
        die(f"в {src} нет обязательных языков: {', '.join(missing)}")
    say(f"языки ({len(names)}, {total / 2**20:.0f} МБ): {', '.join(names)}")


def ascii_path(path):
    """Короткое (8.3) имя пути — оно всегда латиницей, или None.

    Нужно, потому что папку с исходниками никто не обязан держать по латинскому
    пути, а Tesseract кириллицу не открывает: без этого проверка ругалась бы не
    на комплект, а на место, где его собрали.
    """
    if path.isascii():
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf)) and buf.value.isascii():
        return buf.value
    return None


def check_tesseract(tesseract_dir):
    """Проверяем, что скопированный Tesseract запускается сам по себе.

    Если библиотеку недоложили, это видно здесь, а не у человека, которому
    отдали архив.
    """
    exe = os.path.join(tesseract_dir, "tesseract.exe")

    def run(args, env=None):
        try:
            return subprocess.run([exe] + args, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
        except OSError as e:
            die(f"скопированный Tesseract не запускается: {e}\n"
                "Похоже, не хватает какой-то .dll из папки установки.")

    out = run(["--version"])
    if out.returncode != 0:
        die("скопированный Tesseract не работает:\n" + (out.stderr or out.stdout))
    version = (out.stdout or "").splitlines()[0] if out.stdout else "?"

    data = ascii_path(os.path.join(tesseract_dir, "tessdata"))
    if not data:
        say(f"проверка: {version} запускается; языки не проверить — "
            "путь сборки с кириллицей, у человека распакуется по латинскому")
        return
    out = run(["--list-langs"], env=dict(os.environ, TESSDATA_PREFIX=data))
    if out.returncode != 0:
        die("Tesseract в комплекте не видит языков:\n" + (out.stderr or out.stdout))
    langs = [s for s in (out.stdout or "").splitlines() if s and " " not in s]
    say(f"проверка: {version} видит {len(langs)} языков")


# --------------------------------------------------------------------------------------
#  Остальной комплект
# --------------------------------------------------------------------------------------
QUICK_START = """\
ЭКРАННЫЙ ПЕРЕВОДЧИК

Установка не нужна. Распакуйте эту папку целиком (например, в «Документы»)
и запустите «Переводчик.exe».

  ВАЖНО: работать нужно с распакованной папкой, а не изнутри архива.
  Из архива программа не запустится.

При первом запуске Windows может показать синее окно «Windows защитила ваш
компьютер». Это стандартное предупреждение о программе без платной подписи
разработчика: нажмите «Подробнее» -> «Выполнить в любом случае».

Программа не открывает окон, а уходит в трей — синяя иконка «Я» возле часов
(может прятаться под стрелкой «^»).

  Ctrl + Alt + Z   выделить область экрана и перевести
  Ctrl + Alt + X   перевести текст или картинку из буфера обмена
  Esc              закрыть

Правый клик по иконке в трее — язык перевода, оформление, автозапуск, выход.
Нужен интернет: сам перевод делает Google Translate. Распознавание текста
работает без интернета.

Если что-то не работает, запустите «Проверка.bat» — программа сама проверит
распознавание и перевод и напишет, чего не хватает. Снимок этого окна и нужен
автору. «Переводчик (отладка).exe» — то же самое, но с подробным логом работы.

Подробное описание — в файле README.md (открывается Блокнотом).
"""

# Самопроверка живёт в том же .exe: отдельная программа означала бы вторую
# сборку и второй повод ей разойтись с основной.
SELFTEST_BAT = """\
@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0Переводчик (отладка).exe" --selftest
echo.
pause
"""


def public_readme(text):
    """README без внутренней кухни: раздел вырезается вместе с подразделами.

    Один файл на всех вместо двух руками: два разошлись бы через месяц, и в
    комплект однажды уехало бы то, чего там быть не должно.
    """
    out, skip_level = [], None
    for line in text.splitlines(keepends=True):
        head = re.match(r"(#+) ", line)
        if head:
            level = len(head.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None                 # начался соседний раздел
            if line.rstrip() in PRIVATE_SECTIONS:
                skip_level = level
        # строки таблицы настроек про рекламную полосу — это тоже наша кухня
        if skip_level is None and not line.startswith(("| `promo_", "| `footer_")):
            out.append(line)
    text = "".join(out)
    while "\n\n\n" in text:                       # после вырезанных разделов
        text = text.replace("\n\n\n", "\n\n")
    return text


def copy_extras():
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
        full = f.read()
    public = public_readme(full)
    with open(os.path.join(BUNDLE, "README.md"), "w", encoding="utf-8",
              newline="\r\n") as f:
        f.write(public)
    cut = len(full.splitlines()) - len(public.splitlines())
    say(f"README: {cut} строк внутренней кухни отрезано, {len(public.splitlines())} осталось")

    for name in SHIPPED_FILES:
        path = os.path.join(HERE, name)
        if os.path.isfile(path):
            shutil.copy2(path, os.path.join(BUNDLE, name))
    for pattern in SHIPPED_GLOBS:
        for path in glob.glob(os.path.join(HERE, pattern)):
            shutil.copy2(path, os.path.join(BUNDLE, os.path.basename(path)))
    for src, dst in SHIPPED_AS.items():
        path = os.path.join(HERE, src)
        if os.path.isfile(path):
            shutil.copy2(path, os.path.join(BUNDLE, dst))

    with open(os.path.join(BUNDLE, "Как пользоваться.txt"), "w",
              encoding="utf-8-sig", newline="\r\n") as f:
        f.write(QUICK_START)

    # .bat без BOM и с chcp в начале: cmd читает файл построчно, и дальше по
    # тексту русские буквы разбираются уже как UTF-8.
    with open(os.path.join(BUNDLE, "Проверка.bat"), "w",
              encoding="utf-8", newline="\r\n") as f:
        f.write(SELFTEST_BAT)

    # Tesseract распространяется по Apache 2.0 — раз он внутри, лицензия едет с ним.
    notice = os.path.join(BUNDLE, "tesseract", "LICENSE")
    if not os.path.isfile(notice):
        with open(notice, "w", encoding="utf-8") as f:
            f.write("Tesseract OCR, Apache License 2.0\n"
                    "https://github.com/tesseract-ocr/tesseract\n")
    say("добавлены словарь, переводы интерфейса и документация")


def folder_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def app_version():
    """Номер версии берём из самой программы, читая исходник.

    Импортировать модуль ради одной строки нельзя: при импорте он читает
    настройки и тянет за собой все библиотеки. Дата — запасной вариант,
    если константу когда-нибудь переименуют.
    """
    try:
        with open(os.path.join(HERE, "screen_translator.py"), encoding="utf-8") as f:
            m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return datetime.date.today().isoformat()


def make_zip():
    base = os.path.join(DIST, f"ScreenTranslator-{app_version()}")
    for old in glob.glob(base + ".zip"):
        os.remove(old)
    say("упаковываю архив (это дольше всего)…")
    path = shutil.make_archive(base, "zip", root_dir=DIST, base_dir="ScreenTranslator")
    return path


# --------------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="сборка экранного переводчика")
    parser.add_argument("--no-zip", action="store_true",
                        help="не упаковывать в архив")
    parser.add_argument("--keep-langs", default="",
                        help="языки распознавания через запятую, например eng,rus,deu")
    args = parser.parse_args()
    keep = [s.strip() for s in args.keep_langs.split(",") if s.strip()] or None

    started = time.time()
    os.chdir(HERE)
    make_icon()
    run_pyinstaller()
    tesseract_dir = copy_tesseract()
    copy_tessdata(tesseract_dir, keep)
    check_tesseract(tesseract_dir)
    copy_extras()

    say(f"\nпапка готова: {BUNDLE}  ({folder_size(BUNDLE) / 2**20:.0f} МБ)")
    if not args.no_zip:
        archive = make_zip()
        say(f"архив:       {archive}  ({os.path.getsize(archive) / 2**20:.0f} МБ)")
    say(f"заняло {time.time() - started:.0f} с")


if __name__ == "__main__":
    main()
