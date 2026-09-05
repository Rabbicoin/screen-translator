# -*- coding: utf-8 -*-
"""
Screen Translator — экранный переводчик в стиле Google Translate.

Нажал горячую клавишу -> выделил мышкой область экрана -> текст распознаётся (OCR)
и перевод накладывается прямо поверх выделенного места.

Горячие клавиши по умолчанию:
    Ctrl+Alt+Z  — выделить область и перевести
    Ctrl+Alt+X  — перевести текст из буфера обмена
    Esc         — закрыть выделение / окно перевода
"""

# Номер версии живёт здесь и больше нигде: отсюда его берёт подсказка у иконки
# в трее, пункт меню и имя архива при сборке. Без него по отчёту об ошибке
# невозможно понять, какая у человека сборка.
APP_VERSION = "1.0.2"

import ctypes
import glob
import html
import webbrowser
import json
import os
import queue
import re
from concurrent.futures import ThreadPoolExecutor
import shutil
import sys
import tempfile
import threading
import time
import traceback

# консоль Windows по умолчанию не в UTF-8 — иначе русские сообщения превращаются в кракозябры
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- DPI-awareness должен быть выставлен ДО создания окон и снятия скриншота,
# --- иначе на масштабе 125/150% координаты мыши не совпадут с пикселями экрана.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageGrab, ImageOps, ImageStat

import pytesseract
from pytesseract import Output

# Собранная программа (.exe) держит код внутри служебной папки, поэтому
# __file__ там ведёт не туда, где лежат файлы для человека — рядом с .exe.
FROZEN = getattr(sys, "frozen", False)
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
           else os.path.dirname(os.path.abspath(__file__)))

# Запасной латинский путь: у Tesseract беда с кириллицей, а имя пользователя в
# Windows вполне может быть русским. Папка «Общие» лежит по адресу
# C:\Users\Public при любом языке системы.
PUBLIC_DIR = os.path.join(os.environ.get("PUBLIC") or r"C:\Users\Public",
                          "ScreenTranslator")


def _is_writable(path):
    probe = os.path.join(path, ".write_test")
    try:
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


# Настройки, словарь и кэши программа перезаписывает сама. Обычно они лежат
# рядом с ней — так их проще найти и поправить. Но программу могут положить в
# Program Files, куда без администратора не записать; тогда уходим в профиль.
DATA_DIR = APP_DIR if _is_writable(APP_DIR) else os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "ScreenTranslator")
if DATA_DIR != APP_DIR:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        DATA_DIR = APP_DIR


def data_file(name):
    """Путь к файлу, который программа правит. Образец из комплекта переносим."""
    path = os.path.join(DATA_DIR, name)
    if DATA_DIR != APP_DIR and not os.path.exists(path):
        shipped = os.path.join(APP_DIR, name)
        if os.path.isfile(shipped):
            try:
                shutil.copyfile(shipped, path)
            except OSError:
                pass
    return path


CONFIG_PATH = data_file("config.json")
GLOSSARY_PATH = data_file("glossary.json")
OCR_WORDS_PATH = data_file("ocr_words.txt")
DEBUG = os.environ.get("ST_DEBUG") == "1"


def log(*args):
    if DEBUG:
        print("[отладка]", *args, flush=True)

DEFAULT_CONFIG = {
    "hotkey_capture": "<ctrl>+<alt>+z",
    "hotkey_clipboard": "<ctrl>+<alt>+x",
    "target_lang": "ru",
    "translator": "free",       # "free" — публичные точки Google; "deepl"; "google_api"
    "api_key": "",              # ключ для платного сервиса; берётся в его личном кабинете
    "ocr_langs": "auto",            # "auto" — по письменности; иначе, например, "eng+rus"
    "ocr_langs_fallback": "eng+rus",  # чем читать, когда письменность не опознана
    "tesseract_path": "",
    "ocr_scale": 2.0,
    "tesseract_psm": 3,
    "theme": "auto",            # "auto" — как в Windows; "dark"; "light"
    # Единственный канал до людей, которым архив уже отдан: текст лежит на
    # сайте, программа спрашивает его раз в сутки. Очистить — и она не ходит
    # никуда, кроме сервиса перевода.
    "promo_url": "https://sevdev.ru/screen-translator/promo.json",
    "promo_hours": 24,          # как часто его перечитывать
    # Подпись внизу окна перевода: видна сразу и не зависит от сайта. Объявление
    # с сайта, пока оно есть, показывается вместо неё — у новости срок годности,
    # у контактов его нет. Пусто — строки не будет вовсе.
    "footer_text": "Вопросы и пожелания: "
                    "[Telegram @rabbiecho](https://t.me/rabbiecho) · "
                    "[sevdev.ru](https://sevdev.ru)",
    # Запасная ссылка на всю строку — работает, если в тексте нет ссылок в скобках.
    "footer_url": "https://sevdev.ru",
    "display_mode": "overlay",   # "overlay" — поверх области, "panel" — окном с текстом
    "auto_copy": True,
    "font_size": 14,            # кегль в режиме "panel" и в тексте окна
    # --- шрифт перевода в режиме "overlay"
    "overlay_font_scale": 1.0,     # кегль относительно блока; вверх упирается в его размер
    "overlay_zoom": 1.0,           # 1.5 — вся картинка результата в полтора раза крупнее
    "overlay_min_font": 8,         # ниже не ужимаем: что не влезло, режем «…»
    "overlay_line_spacing": 1.25,  # межстрочный интервал; 1.1 экономит место
    "overlay_uniform_font": True,  # один кегль на весь текст, чтобы не прыгал
}


# --------------------------------------------------------------------------------------
#  Конфиг
# --------------------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            print("config.json повреждён, используются настройки по умолчанию")
    else:
        save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


CFG = load_config()
try:
    _CFG_MTIME = os.path.getmtime(CONFIG_PATH)
except OSError:
    _CFG_MTIME = None


def refresh_config():
    """Перечитываем config.json, если он изменился с прошлого раза.

    Нужно, чтобы подбор размера шрифта можно было крутить, не перезапуская
    программу. Горячие клавиши и автозапуск читаются один раз при старте —
    они подхватятся только после перезапуска.
    """
    global CFG, _CFG_MTIME
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return
    if mtime != _CFG_MTIME:
        _CFG_MTIME = mtime
        CFG = load_config()
        log("config.json перечитан")


def _clamp(value, low, high, fallback):
    """Настройку правит человек руками — бережёмся от мусора в config.json."""
    try:
        return min(high, max(low, float(value)))
    except (TypeError, ValueError):
        return fallback


# --------------------------------------------------------------------------------------
#  Tesseract
# --------------------------------------------------------------------------------------
def setup_tesseract():
    """Ищем tesseract.exe: свой из комплекта, потом из конфига и системные."""
    candidates = [
        CFG.get("tesseract_path") or "",
        os.path.join(APP_DIR, "tesseract", "tesseract.exe"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return path
    return "tesseract"  # надежда на PATH


TESSERACT_CMD = setup_tesseract()


# Языки Tesseract, доустановленные пользователем: в Program Files без прав
# администратора не записать. Два ограничения на место:
#   1) путь без кириллицы — Tesseract на Windows такие не открывает (в логе
#      будет «TESSDATA_PREFIX ... does not exist»), поэтому папка рядом с
#      программой годится, только если путь к ней целиком ASCII;
#   2) не внутри AppData — у приложений из Microsoft Store запись туда
#      перенаправляется в их контейнер, и папку не видят ни Проводник, ни
#      программа, запущенная обычным способом.
# Отсюда основное место — папка в корне профиля пользователя.
# Последняя в списке — папка из комплекта собранной программы: языки, которые
# человек доставил себе сам, должны иметь возможность её перекрыть.
TESSDATA_DIRS = [
    os.path.join(os.environ.get("USERPROFILE", ""), "ScreenTranslator", "tessdata"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ScreenTranslator", "tessdata"),
    os.path.join(APP_DIR, "tessdata"),
    os.path.join(APP_DIR, "tesseract", "tessdata"),
]


def short_path(path):
    r"""Короткое (8.3) имя пути — оно всегда латиницей.

    Способ показать Tesseract-у папку, до которой иначе не достучаться: путь
    вида C:\Users\Вася\... превращается в C:\Users\VASYA~1\... Работает не
    везде — короткие имена в системе можно отключить, тогда вернётся исходный
    путь и в дело пойдёт копия из _mirror_tessdata.
    """
    if not path or path.isascii() or sys.platform != "win32":
        return path
    try:
        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf)) and buf.value:
            return buf.value
    except Exception:
        pass
    return path


def _mirror_tessdata(src):
    """Копия языков по латинскому пути — когда короткого имени не досталось.

    Случай редкий (кириллица в пути и отключённые короткие имена), но без копии
    распознавание не заработает вовсе, а 80 МБ копируются один раз.
    """
    dst = os.path.join(PUBLIC_DIR, "tessdata")
    if not dst.isascii():
        return None
    try:
        os.makedirs(dst, exist_ok=True)
        for name in glob.glob(os.path.join(src, "*.traineddata")):
            target = os.path.join(dst, os.path.basename(name))
            if (not os.path.exists(target)
                    or os.path.getsize(target) != os.path.getsize(name)):
                shutil.copyfile(name, target)
    except OSError as e:
        print("не удалось скопировать языки распознавания:", e)
        return None
    return dst if glob.glob(os.path.join(dst, "*.traineddata")) else None


def local_tessdata():
    """Наша папка с языками, если она есть и пригодна. Иначе None — системные."""
    for path in TESSDATA_DIRS:
        if not path or not glob.glob(os.path.join(path, "*.traineddata")):
            continue
        usable = short_path(path)
        if usable.isascii():
            return usable
        mirrored = _mirror_tessdata(path)
        if mirrored:
            return mirrored
    return None


def setup_temp_dir():
    """Временные файлы — тоже по латинскому пути.

    pytesseract сохраняет снимок во временную папку и передаёт Tesseract-у путь
    к нему. У пользователя с русским именем учётной записи путь получается с
    кириллицей, и снимок не открывается — распознавание падает на ровном месте.
    """
    current = tempfile.gettempdir()
    if current.isascii():
        return current
    short = short_path(current)
    if short.isascii():
        tempfile.tempdir = short
        return short
    spare = os.path.join(PUBLIC_DIR, "tmp")
    try:
        os.makedirs(spare, exist_ok=True)
    except OSError:
        return current
    tempfile.tempdir = spare
    return spare


TEMP_DIR = setup_temp_dir()


def setup_tessdata():
    """Показываем Tesseract нашу папку языков через переменную окружения.

    Через --tessdata-dir нельзя: pytesseract разбирает строку конфига shlex-ом,
    и путь с пробелами разваливается на куски.
    """
    path = local_tessdata()
    if path:
        os.environ["TESSDATA_PREFIX"] = path
        log(f"языки OCR берём из {path}")
    return path


LOCAL_TESSDATA = setup_tessdata()


def available_ocr_langs():
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()


# Письменность из OSD -> языки Tesseract, которыми её читать. Берём те из списка,
# что реально установлены; если ни одного нет — откатываемся к настройке.
SCRIPT_LANGS = {
    "Latin": ["eng", "deu", "fra", "spa", "ita", "por", "nld", "pol", "ces", "tur",
              "swe", "dan", "nor", "fin", "ron", "hun", "vie", "ind"],
    "Cyrillic": ["rus", "ukr", "bul", "srp", "kaz", "bel"],
    "Han": ["chi_sim", "chi_tra"],
    "HanS": ["chi_sim"], "HanS_vert": ["chi_sim_vert"],
    "HanT": ["chi_tra"], "HanT_vert": ["chi_tra_vert"],
    "Japanese": ["jpn"], "Japanese_vert": ["jpn_vert"],
    "Korean": ["kor"], "Hangul": ["kor"],
    "Arabic": ["ara", "fas", "urd"],
    "Hebrew": ["heb"], "Greek": ["ell"], "Thai": ["tha"],
    "Devanagari": ["hin", "mar", "nep"], "Bengali": ["ben"],
    "Tamil": ["tam"], "Telugu": ["tel"], "Kannada": ["kan"], "Malayalam": ["mal"],
    "Gujarati": ["guj"], "Gurmukhi": ["pan"], "Oriya": ["ori"], "Sinhala": ["sin"],
    "Georgian": ["kat"], "Armenian": ["hye"], "Ethiopic": ["amh"],
    "Khmer": ["khm"], "Lao": ["lao"], "Myanmar": ["mya"], "Tibetan": ["bod"],
    "Syriac": ["syr"], "Cherokee": ["chr"],
}


def detect_script(img):
    """Какой письменностью набран текст: Latin, Cyrillic, Han, Arabic и т. д.

    Tesseract умеет это отдельным проходом (OSD) — он быстрый и не зависит от
    того, какие языки установлены. По письменности дальше выбираем языки:
    гонять распознавание сразу всеми установленными и медленно, и менее точно.
    """
    try:
        osd = pytesseract.image_to_osd(
            img, output_type=Output.DICT,
            config="--psm 0 -c min_characters_to_try=8")
        return osd.get("script"), float(osd.get("script_conf") or 0)
    except Exception as e:
        log("OSD не определил письменность:", e)
        return None, 0.0


# Ниже этого порога OSD ошибается: на коротком тексте он уверенно называет
# латиницу кириллицей. Такой догадке лучше не верить и взять запасной набор.
OSD_MIN_CONF = 1.5

# Письмо справа налево: слова в строке идут от правого края к левому, поэтому
# склеивать их надо в обратном порядке относительно координат. Решаем по самим
# символам, а не по списку языков: в наборе может оказаться и арабский, и
# китайский разом — тогда по списку строка перевернулась бы вся.
RTL_RANGES = ((0x0590, 0x05FF), (0x0600, 0x06FF), (0x0700, 0x074F),
              (0x0750, 0x077F), (0x0780, 0x07BF), (0x08A0, 0x08FF),
              (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))


def _is_rtl_char(ch):
    return any(a <= ord(ch) <= b for a, b in RTL_RANGES)


def _is_rtl_text(text):
    """Строка написана справа налево, если такие буквы в ней преобладают."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if _is_rtl_char(ch)) > len(letters) / 2


# Скобки и кавычки в письме справа налево смотрят в другую сторону
_MIRRORED = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
             "<": ">", ">": "<", "«": "»", "»": "«"}


def bidi_display(text):
    """Логический порядок букв -> зрительный, для строк справа налево.

    Ни Pillow, ни Tk сами этого не делают: буквы кладутся слева направо в том
    порядке, в каком лежат в строке, и еврейская фраза читается наоборот — это
    и видно было на картинке. Разворачиваем сами.

    Куски на латинице и числа («$42.99», «16 x 12») внутри разворачивать нельзя,
    они читаются слева направо, — поэтому строка режется на куски по
    направлению, и переставляются куски целиком.
    """
    if not _is_rtl_text(text):
        return text

    runs = []
    for ch in text:
        if _is_rtl_char(ch):
            kind = "R"
        elif ch.isalnum():
            kind = "L"
        else:
            kind = "N"                      # пробелы, знаки — примкнут к соседям
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(ch)
        else:
            runs.append([kind, [ch]])

    # нейтральный кусок между двумя латинскими остаётся с ними, иначе идёт
    # по направлению строки: «цена $42.99 за штуку» не должна рассыпаться
    for i, run in enumerate(runs):
        if run[0] != "N":
            continue
        left = runs[i - 1][0] if i else "R"
        right = runs[i + 1][0] if i + 1 < len(runs) else "R"
        run[0] = "L" if left == "L" and right == "L" else "R"

    out = []
    for kind, chars in reversed(runs):
        if kind == "L":
            out.append("".join(chars))
        else:
            out.append("".join(_MIRRORED.get(c, c) for c in reversed(chars)))
    return "".join(out)

# Коды Tesseract -> коды переводчика. Нужны запасному сервису: у него нет
# автоопределения, и без подсказки он считал любой текст английским.
TESS_TO_ISO = {
    "eng": "en", "rus": "ru", "deu": "de", "fra": "fr", "spa": "es", "ita": "it",
    "por": "pt", "nld": "nl", "pol": "pl", "ces": "cs", "tur": "tr", "swe": "sv",
    "dan": "da", "nor": "no", "fin": "fi", "ron": "ro", "hun": "hu", "vie": "vi",
    "ind": "id", "ukr": "uk", "bul": "bg", "srp": "sr", "kaz": "kk", "bel": "be",
    "chi_sim": "zh-CN", "chi_tra": "zh-TW", "jpn": "ja", "kor": "ko",
    "ara": "ar", "fas": "fa", "urd": "ur", "heb": "he", "ell": "el", "tha": "th",
    "hin": "hi", "mar": "mr", "nep": "ne", "ben": "bn", "tam": "ta", "tel": "te",
    "kan": "kn", "mal": "ml", "guj": "gu", "pan": "pa", "ori": "or", "sin": "si",
    "kat": "ka", "hye": "hy", "amh": "am", "khm": "km", "lao": "lo", "mya": "my",
}


# Язык перевода -> модель Tesseract. На экране обычно есть и то, что переводим,
# и уже переведённое рядом, поэтому язык перевода в набор добавляем всегда.
ISO_TO_TESS = {}
for _tess, _iso in TESS_TO_ISO.items():
    ISO_TO_TESS.setdefault(_iso, _tess)


# --------------------------------------------------------------------------------------
#  Объявление автора
# --------------------------------------------------------------------------------------
# Строка внизу окна результата: новая версия, платная версия, что угодно. Текст
# лежит на сайте автора, программа забирает его раз в сутки и запоминает — сайт
# может быть недоступен, объявление от этого не должно мигать. Пока адрес в
# настройках пустой, программа не ходит никуда и полосу не рисует.
PROMO_LIMIT = 140              # длиннее в панель не влезет, да и читать не станут
_promo = {"data": None, "checked": 0.0}


def _promo_path():
    return data_file("promo_cache.json")


LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def promo_parts(text, fallback_url=""):
    """Строка подписи → куски [(текст, ссылка или "")].

    Ссылки пишутся прямо в тексте: "пишите в [Telegram](https://t.me/имя)".
    Так в одной строке живут два разных адреса — раньше вся строка вела на один,
    и щелчок по слову «Telegram» открывал сайт.

    Разметки нет — строка остаётся одним куском с запасной ссылкой, как раньше.
    Длину считаем по видимому тексту, иначе обрезка рубит середину адреса.
    """
    parts, pos, shown = [], 0, 0
    for m in LINK_RE.finditer(text):
        for chunk, url in ((text[pos:m.start()], ""), (m.group(1), m.group(2))):
            if chunk and shown < PROMO_LIMIT:
                chunk = chunk[:PROMO_LIMIT - shown]
                shown += len(chunk)
                parts.append((chunk, url))
        pos = m.end()
    tail = text[pos:]
    if tail and shown < PROMO_LIMIT:
        parts.append((tail[:PROMO_LIMIT - shown], "" if parts else fallback_url))
    if not parts:
        return []
    if len(parts) == 1 and not parts[0][1]:
        return [(parts[0][0], fallback_url)]
    return parts


def _footer_line():
    """Постоянная подпись из настроек: контакты автора. Или None."""
    text = " ".join(str(CFG.get("footer_text", "") or "").split())
    if not text:
        return None
    url = str(CFG.get("footer_url", "") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        url = ""
    return text, url


def promo_line():
    """Что показывать сейчас: (текст, ссылка) или None.

    Объявление с сайта — временное: у него есть срок, и когда он вышел или
    сайта нет вовсе, остаётся постоянная подпись с контактами. Держать контакты
    только на сайте нельзя: человек с готовым архивом должен видеть, куда
    писать, даже если сайт лежит или ещё не поднят.

    Ссылку берём только http(s): в файле с чужого сервера может оказаться что
    угодно, а открывать по щелчку локальные пути программа не должна.
    """
    data = _promo.get("data")
    if not isinstance(data, dict):
        return _footer_line()
    text = " ".join(str(data.get("text") or "").split())
    if not text:
        return _footer_line()
    until = str(data.get("until") or "").strip()
    if until and time.strftime("%Y-%m-%d") > until:
        return _footer_line()             # срок показа вышел
    url = str(data.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        url = ""
    return text, url


def load_promo():
    """Достаём вчерашнее объявление из файла — до ответа сайта показываем его."""
    try:
        with open(_promo_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _promo["data"] = data
            _promo["checked"] = os.path.getmtime(_promo_path())
    except Exception:
        pass


def fetch_promo():
    """Раз в сутки спрашиваем свежее объявление. Адрес пустой — не ходим никуда."""
    url = str(CFG.get("promo_url", "") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return
    hours = _clamp(CFG.get("promo_hours", 24), 1, 24 * 30, 24)
    if time.time() - _promo["checked"] < hours * 3600:
        return
    _promo["checked"] = time.time()

    def work():
        try:
            r = requests.get(url, headers=UA, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                return
            _promo["data"] = data
            with open(_promo_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            log("объявление обновлено")
        except Exception as e:
            log("объявление не получено:", e)

    threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------------------------------
#  Оформление
# --------------------------------------------------------------------------------------
# Цвета названы по назначению, а не по виду: «фон», «панель», «приглушённый».
# Иначе при светлой теме в коде остаются переменные вроде «тёмно-серый», который
# на самом деле светлый, и разобраться в этом потом невозможно.
THEMES = {
    "dark": {
        "bg": "#1e1e1e", "fg": "#f2f2f2", "bar": "#151515", "btn": "#dddddd",
        "hover": "#2a2a2a", "active": "#2a2a2a", "active_fg": "#ffffff",
        "hover_active": "#3a3a3a", "dim": "#8a8a8a", "faint": "#6f6f6f",
        "line": "#3a3a3a", "grip": "#7a7a7a", "accent": "#1a73e8",
        "accent_fg": "#ffffff", "close": "#c0392b", "sb_thumb": "#4a4a4a",
        "sb_active": "#6a6a6a", "tip_bg": "#2a2a2a", "entry": "#2a2a2a",
        "slider": "#c9c9c9", "trough": "#333333",
    },
    "light": {
        "bg": "#fbfbfb", "fg": "#1b1b1b", "bar": "#e9edf0", "btn": "#2f3438",
        "hover": "#dbe0e5", "active": "#cfd6dc", "active_fg": "#000000",
        "hover_active": "#c2cad1", "dim": "#5f6771", "faint": "#8a9098",
        "line": "#c3cad1", "grip": "#7c848c", "accent": "#1a73e8",
        "accent_fg": "#ffffff", "close": "#c0392b", "sb_thumb": "#b6bec6",
        "sb_active": "#949da6", "tip_bg": "#ffffff", "entry": "#ffffff",
        "slider": "#5f6771", "trough": "#c3cad1",
    },
}


def windows_theme():
    """Светлая тема в Windows или тёмная. Не вышло спросить — считаем тёмной."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if light else "dark"
    except Exception:
        return "dark"


def theme_name():
    """Имя текущей палитры: «dark» или «light». «auto» — как у системы."""
    want = str(CFG.get("theme", "auto") or "auto").lower()
    return want if want in THEMES else windows_theme()


def theme():
    """Палитра под текущую настройку."""
    return THEMES[theme_name()]


# --------------------------------------------------------------------------------------
#  Надписи интерфейса
# --------------------------------------------------------------------------------------
# Язык интерфейса = язык перевода: человек выбрал «переводить на немецкий» —
# значит и кнопки ему удобнее видеть по-немецки. Русский и английский написаны
# руками, остальные переводятся тем же сервисом один раз и ложатся в файл рядом
# с настройками. Ни одна строка не содержит подстановок: машинный перевод
# норовит утащить «{}» в другое место или потерять вовсе.
UI_STRINGS = {
    "mode_image":    ("Картинкой", "Image"),
    "mode_orig":     ("Текст оригинал", "Original text"),
    "mode_trans":    ("Текст перевод", "Translation"),
    "copy":          ("Копировать", "Copy the text"),
    "copy_orig":     ("Копировать оригинал", "Copy original"),
    "copy_trans":    ("Копировать перевод", "Copy translation"),
    "no_text":       ("(текст не распознан)", "(no text recognized)"),
    "target_tip":    ("Язык перевода", "Translation language"),
    "translating":   ("Перевожу…", "Translating…"),
    "working":       ("Распознаю и перевожу…", "Recognizing and translating…"),
    "not_recognized": ("Текст не распознан.\nПопробуйте выделить область покрупнее "
                       "или увеличьте масштаб исходного текста.",
                       "No text recognized.\nTry selecting a larger area "
                       "or zoom in on the original text."),
    "error":         ("Ошибка", "Error"),  # дальше двоеточие и текст сбоя
    "clip_empty":    ("В буфере обмена нет ни текста, ни картинки",
                      "The clipboard has neither text nor an image"),
    "font_size":     ("Шрифт перевода", "Translation font"),
    "autostart_on":  ("Автозапуск включён", "Startup enabled"),
    "autostart_off": ("Автозапуск выключен", "Startup disabled"),
    "target_set":    ("Переводить на", "Translate into"),
    "ocr_set":       ("Язык на экране", "Screen language"),
    "service_set":   ("Сервис перевода", "Translation service"),
    "key_saved":     ("Ключ сохранён", "Key saved"),
    "key_removed":   ("Ключ удалён", "Key removed"),
    "menu_capture":  ("Перевести область", "Translate an area"),
    "menu_clip":     ("Перевести буфер", "Translate the clipboard"),
    "menu_key":      ("Ввести ключ…", "Enter the API key…"),
    "menu_theme":    ("Оформление", "Appearance"),
    "theme_auto":    ("Как в Windows", "Same as Windows"),
    "theme_light":   ("Светлое", "Light"),
    "theme_dark":    ("Тёмное", "Dark"),
    "font_bigger":   ("Шрифт перевода крупнее", "Larger translation font"),
    "font_smaller":  ("Шрифт перевода мельче", "Smaller translation font"),
    "autostart":     ("Запускать при включении компьютера", "Run at startup"),
    "settings":      ("Открыть настройки", "Open settings"),
    "copy_sel":      ("Копировать выделенное", "Copy selection"),
    "select_all":    ("Выделить всё", "Select all"),
    "quit":          ("Выход", "Exit the program"),
    "ocr_auto":      ("Определять автоматически", "Detect automatically"),
    "svc_free":      ("Бесплатный (Google, с лимитами)", "Free (Google, rate-limited)"),
    "svc_deepl":     ("DeepL — по своему ключу", "DeepL — with your own key"),
    "svc_google":    ("Google Cloud — по своему ключу", "Google Cloud — with your own key"),
    "key_title":     ("Ключ для перевода", "Translation key"),
    "key_deepl":     ("Ключ берётся на deepl.com/pro-api, раздел Account.\n"
                      "Бесплатный ключ оканчивается на «:fx» — его тоже можно.",
                      "Get the key at deepl.com/pro-api, the Account tab.\n"
                      "A free key ends with «:fx» — that one works too."),
    "key_google":    ("Ключ берётся в console.cloud.google.com:\n"
                      "включите Cloud Translation API и создайте ключ.",
                      "Get the key at console.cloud.google.com:\n"
                      "enable the Cloud Translation API and create a key."),
    "key_pick":      ("Сначала выберите платный сервис в меню «Сервис перевода».",
                      "First pick a paid service in the «Translation service» menu."),
    "save":          ("Сохранить", "Save"),
    "cancel":        ("Отмена", "Cancel"),
}

_ui_cache = {}          # язык -> {ключ: перевод}
_ui_pending = set()     # какие языки уже переводятся, чтобы не просить дважды


def ui_lang():
    return str(CFG.get("target_lang", "ru") or "ru").lower()


def _ui_path(lang):
    safe = re.sub(r"[^a-z0-9_-]", "", lang)
    return data_file(f"ui_{safe}.json")


def tr(key):
    """Надпись на языке перевода. Нет готового — берём английскую."""
    ru, en = UI_STRINGS.get(key, (key, key))
    lang = ui_lang()
    if lang.startswith("ru"):
        return ru
    if lang.startswith("en"):
        return en
    return _ui_cache.get(lang, {}).get(key, en)


def load_ui_language(lang):
    """Достаём готовые надписи из файла рядом с настройками."""
    if lang in _ui_cache or lang.startswith(("ru", "en")):
        return True
    try:
        with open(_ui_path(lang), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            _ui_cache[lang] = data
            return True
    except Exception:
        pass
    return False


def fetch_ui_language(lang, done=None):
    """Переводим надписи интерфейса на новый язык — один раз в жизни.

    Делается в фоне: пока не готово, интерфейс говорит по-английски, а как
    только перевод пришёл — окна и меню пересобираются. Английский берём за
    исходник: с него машинный перевод коротких подписей выходит заметно точнее,
    чем с русского.
    """
    lang = (lang or "").lower()
    if lang.startswith(("ru", "en")) or lang in _ui_cache or lang in _ui_pending:
        return
    if load_ui_language(lang):
        return
    _ui_pending.add(lang)

    def work():
        try:
            keys = list(UI_STRINGS)
            parts = translate_many([UI_STRINGS[k][1] for k in keys], lang, "en")
            got = {k: v.strip() for k, v in zip(keys, parts) if v and v.strip()}
            if len(got) < len(keys) * 0.8:      # половина не перевелась — не портим вид
                return
            _ui_cache[lang] = got
            try:
                with open(_ui_path(lang), "w", encoding="utf-8") as f:
                    json.dump(got, f, ensure_ascii=False, indent=1)
            except Exception:
                pass
            if done:
                done()
        except Exception as e:
            log("надписи интерфейса не перевелись:", e)
        finally:
            _ui_pending.discard(lang)

    threading.Thread(target=work, daemon=True).start()


# Языки, которых нет в списках выбора, даже если распознавание их умеет.
# Меню «Язык на экране» строится по установленным файлам *.traineddata, так что
# одним удалением названия его оттуда не убрать — нужен явный список.
HIDDEN_OCR_LANGS = {"ukr"}

# Названия для меню в трее. Коды Tesseract человеку ничего не говорят.
LANG_NAMES = {
    "eng": "английский", "rus": "русский", "deu": "немецкий", "fra": "французский",
    "spa": "испанский", "ita": "итальянский", "por": "португальский",
    "nld": "нидерландский", "pol": "польский", "ces": "чешский", "tur": "турецкий",
    "swe": "шведский", "dan": "датский", "nor": "норвежский", "fin": "финский",
    "ron": "румынский", "hun": "венгерский", "vie": "вьетнамский",
    "ind": "индонезийский", "ukr": "украинский", "bul": "болгарский",
    "srp": "сербский", "kaz": "казахский", "bel": "белорусский",
    "chi_sim": "китайский упрощённый", "chi_tra": "китайский традиционный",
    "jpn": "японский", "kor": "корейский", "ara": "арабский", "fas": "персидский",
    "urd": "урду", "heb": "иврит", "ell": "греческий", "tha": "тайский",
    "hin": "хинди", "mar": "маратхи", "nep": "непальский", "ben": "бенгальский",
    "tam": "тамильский", "tel": "телугу", "kan": "каннада", "mal": "малаялам",
    "guj": "гуджарати", "pan": "панджаби", "ori": "ория", "sin": "сингальский",
    "kat": "грузинский", "hye": "армянский", "amh": "амхарский", "khm": "кхмерский",
    "lao": "лаосский", "mya": "бирманский",
}


# Языки перевода для меню. Сервисы знают их сотню, но в списке, который
# открывают мышью, нужны те, на которые переводят на самом деле. Кому нужен
# редкий — вписывает код в config.json руками.
# Каждый язык назван на себе самом: так список читается при любом языке
# интерфейса — немцу «Deutsch» понятнее, чем «немецкий» или «German».
TARGET_LANGS = [
    ("ru", "Русский"), ("en", "English"), ("de", "Deutsch"),
    ("fr", "Français"), ("es", "Español"), ("it", "Italiano"),
    ("pt", "Português"), ("pl", "Polski"), ("nl", "Nederlands"),
    ("cs", "Čeština"), ("sv", "Svenska"),
    ("tr", "Türkçe"), ("ar", "العربية"), ("he", "עברית"),
    ("zh-CN", "中文"), ("ja", "日本語"), ("ko", "한국어"),
    ("hi", "हिन्दी"), ("id", "Bahasa Indonesia"), ("vi", "Tiếng Việt"),
    ("kk", "Қазақша"), ("be", "Беларуская"),
]
TARGET_NAMES = dict(TARGET_LANGS)


_globe = None


def globe_icon(widget):
    """Значок глобуса для кнопки языка.

    Глобус живёт выше U+FFFF, а Tcl до 8.6.10 такие символы не принимает и
    роняет создание виджета. Проверяем это один раз, и если не вышло — берём
    «文A», знакомый по кнопкам переключения языка в других программах.
    """
    global _globe
    if _globe is None:
        try:
            tk.Label(widget, text="\U0001F310").destroy()
            _globe = "\U0001F310"
        except Exception:
            _globe = "文A"
    return _globe


def target_title(code):
    """«de» -> «Deutsch». Незнакомый код показываем как есть."""
    return TARGET_NAMES.get(code, code)


def lang_title(code):
    """«eng+rus» -> «Английский + русский», «auto» -> «Определять автоматически»."""
    if not code or code == "auto":
        return tr("ocr_auto")
    names = [LANG_NAMES.get(part, part) for part in code.split("+")]
    title = " + ".join(names)
    return title[:1].upper() + title[1:]


# Иероглифы и слоговые азбуки OSD путает между собой: китайскую строку он
# называет то Han, то Japanese. Проверено, что общий набор моделей ничего не
# портит — японский и корейский узнаются с ним так же точно, — поэтому для всей
# группы берём один список и снимаем вопрос.
CJK_SCRIPTS = {"Han", "HanS", "HanT", "HanS_vert", "HanT_vert",
               "Japanese", "Japanese_vert", "Korean", "Hangul"}
CJK_LANGS = ["chi_sim", "chi_tra", "jpn", "kor"]


def ocr_lang_candidates(img):
    """Наборы языков в порядке попыток. Обычно срабатывает первый.

    Запасные нужны потому, что на короткой строке определитель письменности
    ошибается уверенно: китайский текст он называет кириллицей, и распознавание
    не находит вообще ничего. Одной догадке доверять нельзя.
    """
    want = str(CFG.get("ocr_langs", "auto") or "").strip()
    if want.lower() != "auto":
        return [want]

    have = available_ocr_langs() - {"osd"}
    fallback = str(CFG.get("ocr_langs_fallback", "eng+rus") or "eng").strip()
    named = [l for l in fallback.replace("+", " ").split() if l in have]
    target = ISO_TO_TESS.get(str(CFG.get("target_lang", "ru")).lower())
    if target and target in have and target not in named:
        named.append(target)
    script, conf = detect_script(img)
    names = CJK_LANGS if script in CJK_SCRIPTS else SCRIPT_LANGS.get(script or "", [])
    langs = [l for l in names if l in have]

    order = []
    # Порог доверия нужен только там, где запасной набор эту письменность и так
    # читает: спутать латиницу с кириллицей не страшно, «eng+rus» покроет обе.
    # А для иероглифов или арабицы откат — гарантированно пустой результат.
    covered = set(langs) & set(fallback.replace("+", " ").split())
    if langs and not (conf < OSD_MIN_CONF and covered):
        # Полный список письменности — это девять моделей на одну латиницу: втрое
        # медленнее и без всякой пользы, если языки в кадре и так свои. Поэтому
        # первым идёт узкий набор — то, что названо в настройке, плюс язык
        # перевода. Полный список остаётся следующей попыткой, если узкий не
        # справится. Письменность, которую настройка не покрывает (иероглифы,
        # арабица), сужать нечем — там сразу полный список.
        narrow = named if covered else []
        if narrow and "+".join(narrow) not in order:
            order.append("+".join(narrow))
        # Иероглифы: набор из четырёх языков сразу читает ХУЖЕ одного — модели
        # мешают друг другу. На сканированной инструкции «chi_sim» находит
        # подписи, которых «chi_sim+chi_tra+jpn+kor» не видит, и тратит вдвое
        # меньше времени. Поэтому языки этой письменности пробуем поодиночке, а
        # общий набор оставляем следующей попыткой — на случай, когда
        # определитель ошибся упрощённым вместо традиционного.
        if script in CJK_SCRIPTS:
            for one in SCRIPT_LANGS.get(script or "", []):
                if one in have and one not in order:
                    order.append(one)
        if "+".join(langs) not in order:
            order.append("+".join(langs))
    if fallback and fallback not in order:
        order.append(fallback)

    # Последняя попытка — всё, что установлено. Нужна для картинок, где языки
    # смешаны: одна письменность на всю картинку там не работает в принципе.
    # Медленнее (секунды вместо доли секунды), но включается только когда узкий
    # набор явно не справился.
    wide = "+".join(sorted(have))
    if wide and wide not in order:
        order.append(wide)
    global LAST_SCRIPT
    LAST_SCRIPT = script
    log(f"письменность {script or 'не определена'} (уверенность {conf:.1f}), "
        f"порядок попыток: {order}")
    return order or ["eng"]


# --------------------------------------------------------------------------------------
#  Автозапуск при входе в Windows
# --------------------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ScreenTranslator"


def autostart_command():
    """Команда запуска. У собранной программы это она сама, у исходника —
    pythonw, чтобы не мелькало окно консоли."""
    if FROZEN:
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_NAME)
        return bool(value)
    except OSError:
        return False


def set_autostart(enabled):
    """Включаем/выключаем автозапуск. Пишем только в ветку текущего пользователя."""
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print("Не удалось изменить автозапуск:", e)
        return False


# --------------------------------------------------------------------------------------
#  Глобальные горячие клавиши (WinAPI RegisterHotKey — не зависит от раскладки)
# --------------------------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312

VK_NAMES = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "print_screen": 0x2C, "prtsc": 0x2C, "pause": 0x13,
    "grave": 0xC0, "tilde": 0xC0, "`": 0xC0, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def parse_hotkey(text):
    """'<ctrl>+<alt>+z' -> (модификаторы, код клавиши)."""
    mods, vk = 0, None
    for part in str(text).split("+"):
        key = part.strip().strip("<>").lower()
        if key in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif key in ("alt", "menu"):
            mods |= MOD_ALT
        elif key == "shift":
            mods |= MOD_SHIFT
        elif key in ("win", "super", "cmd"):
            mods |= MOD_WIN
        elif not key:
            continue
        elif len(key) == 1 and (key.isalnum()):
            vk = ord(key.upper())
        elif re.fullmatch(r"f\d{1,2}", key):
            vk = 0x70 + int(key[1:]) - 1
        elif key in VK_NAMES:
            vk = VK_NAMES[key]
    return mods, vk


def register_global_hotkeys(mapping):
    """Регистрируем хоткеи и крутим очередь сообщений в фоновом потоке."""
    user32 = ctypes.windll.user32
    user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = ctypes.c_bool

    def loop():
        from ctypes import wintypes
        handlers = {}
        for hotkey_id, (text, callback) in enumerate(mapping.items(), start=1):
            mods, vk = parse_hotkey(text)
            if not vk:
                print(f"  [!] не понял сочетание {text!r}")
                continue
            if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                handlers[hotkey_id] = callback
                log(f"зарегистрирован хоткей {text} (mods={mods}, vk={vk})")
            else:
                print(f"  [!] сочетание {text} занято другой программой — "
                      f"поменяйте hotkey в config.json")
        if not handlers:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                log("нажат хоткей", msg.wParam)
                handler = handlers.get(msg.wParam)
                if handler:
                    try:
                        handler()
                    except Exception:
                        traceback.print_exc()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------------------
#  Экран
# --------------------------------------------------------------------------------------
def virtual_screen_rect():
    """Прямоугольник всего рабочего пространства (все мониторы). Может иметь минусовые X/Y."""
    if sys.platform == "win32":
        u = ctypes.windll.user32
        x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        return x, y, w, h
    return 0, 0, 1920, 1080


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def monitor_rect_at(x, y):
    """Рабочая область монитора под точкой (без панели задач).

    Нужна окну результата: на нескольких мониторах размеры и координаты разные,
    и держать окно надо на том экране, куда его перетащили, а не на главном.
    При любой осечке откатываемся ко всему рабочему пространству.
    """
    if sys.platform != "win32":
        return virtual_screen_rect()
    try:
        u = ctypes.windll.user32
        hmon = u.MonitorFromPoint(_POINT(int(x), int(y)), 2)   # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return virtual_screen_rect()
        r = mi.rcWork
        if r.right - r.left < 100 or r.bottom - r.top < 100:
            return virtual_screen_rect()
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return virtual_screen_rect()


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")


def clipboard_image():
    """Картинка из буфера обмена: либо сам снимок, либо скопированный файл."""
    try:
        data = ImageGrab.grabclipboard()
    except Exception as e:
        log("не смог прочитать картинку из буфера:", e)
        return None
    if isinstance(data, Image.Image):
        return data.convert("RGB")
    if isinstance(data, list):
        for path in data:
            if str(path).lower().endswith(IMAGE_EXTS) and os.path.isfile(path):
                try:
                    return Image.open(path).convert("RGB")
                except Exception:
                    continue
    return None


def grab_screen():
    """Снимок всех мониторов + координаты его левого верхнего угла."""
    vx, vy, vw, vh = virtual_screen_rect()
    try:
        img = ImageGrab.grab(bbox=(vx, vy, vx + vw, vy + vh), all_screens=True)
    except TypeError:  # старые Pillow без all_screens
        img = ImageGrab.grab()
        vx = vy = 0
    return img.convert("RGB"), vx, vy


# --------------------------------------------------------------------------------------
#  OCR
# --------------------------------------------------------------------------------------

def preprocess(img, scale):
    """Апскейл + ч/б + автоконтраст: заметно поднимает точность на мелком экранном шрифте."""
    if scale and scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                         Image.LANCZOS)
    img = ImageOps.autocontrast(img.convert("L"))
    return img


def _column_gaps(words, width, min_gap):
    """Сквозные вертикальные «рвы» — пустые полосы между колонками и карточками.

    Обычный пробел внутри строки рвом не считается: в соседних строках на этом
    же месте стоят слова, а между колонками пусто по всей высоте области.
    """
    covered = bytearray(width)
    for word in words:
        for x in range(max(0, word["x0"]), min(width, word["x1"])):
            covered[x] = 1
    gaps, x = [], 0
    while x < width:
        if covered[x]:
            x += 1
            continue
        start = x
        while x < width and not covered[x]:
            x += 1
        if x - start >= min_gap and start > 0 and x < width:
            gaps.append((start, x))
    return gaps


# Письменность, определённая для последнего снимка. Нужна второму проходу
# распознавания, чтобы не спрашивать её повторно (это лишние 0,3 секунды).
LAST_SCRIPT = None


def narrow_langs(langs, script):
    """Узкий набор для второго прохода: языки выбранного набора, родные для
    письменности на картинке. Для «eng+rus» на кириллице это «rus».

    Пустая строка означает «второй проход не нужен»: набор и так из одного
    языка либо целиком чужой определённой письменности.
    """
    parts = [p for p in langs.split("+") if p]
    if len(parts) < 2:
        return ""
    native = CJK_LANGS if script in CJK_SCRIPTS else SCRIPT_LANGS.get(script or "", [])
    inner = [p for p in parts if p in native]
    return "+".join(inner) if inner and len(inner) < len(parts) else ""


def mixed_scripts(words, least=3):
    """На картинке есть слова и латиницей, и кириллицей — не меньше «least» тех
    и других. Только в этом случае второй проход имеет смысл: на одноязычном
    снимке путать нечего, а лишние две секунды никому не нужны.
    """
    latin = cyrillic = 0
    for word in words:
        letters = [c for c in word["text"] if c.isalpha()]
        if not letters:
            continue
        if sum(1 for c in letters if "\u0400" <= c <= "\u04FF") > len(letters) / 2:
            cyrillic += 1
        else:
            latin += 1
        if latin >= least and cyrillic >= least:
            return True
    return False


def _digit_soup(text):
    """Цифры вперемешку с буквами внутри слова: «6e3», «1PX7»."""
    return any(c.isalpha() for c in text) and any(c.isdigit() for c in text)


def merge_readings(primary, other):
    """Берём из второго прохода те слова, что прочитаны увереннее.

    Смешанный набор («eng+rus») читает латиницу правильно, но кириллицу
    подменяет похожими латинскими буквами: «без НДС» превращается в «6e3 HOC»,
    «АКПП» в «AKNN». Узкий набор («rus») читает кириллицу верно, но латинские
    названия теряет целиком — по отдельности не годится ни тот, ни другой.

    Слова сопоставляются по месту на картинке, а выбор решает уверенность
    Tesseract. Там, где узкий набор увереннее, он и прав («НДС» 89 против «HOC»
    67); там, где увереннее смешанный, это как раз латинское название («NISSAN»
    92 против «№ЗЗАМ» 53), и его мы не трогаем.
    """
    pool = [w for words in other.values() for w in words]
    if not pool:
        return 0
    fixed = 0
    for words in primary.values():
        for word in words:
            area = max(1, (word["x1"] - word["x0"]) * (word["y1"] - word["y0"]))
            best, cover = None, 0.0
            for cand in pool:
                dx = min(word["x1"], cand["x1"]) - max(word["x0"], cand["x0"])
                dy = min(word["y1"], cand["y1"]) - max(word["y0"], cand["y0"])
                if dx <= 0 or dy <= 0:
                    continue
                share = dx * dy / area
                if share > cover:
                    best, cover = cand, share
            if not best or cover < 0.6 or best["text"] == word["text"]:
                continue
            take = best["conf"] > word["conf"]
            if not take and best["conf"] >= word["conf"] - 3:
                # Ничья по уверенности. Смешанный набор любит подменять буквы
                # похожими цифрами: «без» -> «6e3», «шт.» -> «wT.». Если у него
                # в слове цифры вперемешку с буквами, а у узкого — чистые
                # буквы, прав узкий. Латинские артикулы («VQ23DE») правило не
                # задевает: там цифры есть у обоих прочтений.
                take = _digit_soup(word["text"]) and not _digit_soup(best["text"])
            if take:
                word["text"], word["conf"] = best["text"], best["conf"]
                fixed += 1
    return fixed


def _ocr_lines(img):
    """Распознанные строки: [{text, box, conf, col}], сверху вниз.

    Строку режем на части там, где между словами большой горизонтальный провал —
    иначе текст из соседних колонок (карточек) склеивается в одну кашу.
    """
    scale = float(CFG.get("ocr_scale", 2.0) or 1.0)
    # для однострочных полосок psm 7 (одна строка) точнее, чем разметка страницы
    psm = 7 if img.height < 40 else int(CFG.get("tesseract_psm", 3))
    prepared = preprocess(img, scale)

    def read(langs):
        """Один проход распознавания -> строки, сгруппированные Tesseract-ом."""
        try:
            data = pytesseract.image_to_data(prepared, lang=langs,
                                             config=f"--oem 3 --psm {psm}",
                                             output_type=Output.DICT)
        except UnicodeDecodeError:
            # pytesseract читает сообщения tesseract как UTF-8, а на русской
            # Windows они в cp1251 — настоящая ошибка тонет в этом падении
            raise RuntimeError(f"Tesseract не смог прочитать языки «{langs}». "
                               f"Проверьте, что они установлены.")
        found, dropped = {}, 0
        for i in range(len(data["text"])):
            word = (data["text"][i] or "").strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if not word:
                continue
            if conf < 40:
                # Tesseract что-то здесь видит, но прочитать не может — обычно
                # это текст на письменности, которой нет в наборе
                dropped += 1
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            found.setdefault(key, []).append({"x0": x, "y0": y, "x1": x + w, "y1": y + h,
                                              "text": word, "conf": conf})
        return found, sum(len(v) for v in found.values()), dropped

    # Один проход не всегда справляется: письменность могла определиться неверно,
    # а на смешанной картинке её вообще нельзя выбрать одну на всех. Поэтому
    # пробуем наборы по очереди и берём тот, что прочитал больше слов. Много
    # отброшенных слов — признак, что там текст на неохваченной письменности.
    candidates = ocr_lang_candidates(prepared)
    lines, langs, best_kept = {}, candidates[0], -1
    for attempt in candidates:
        found, kept, dropped = read(attempt)
        log(f"«{attempt}»: прочитано {kept}, отброшено {dropped}")
        if kept > best_kept:
            lines, langs, best_kept = found, attempt, kept
        if kept and dropped <= max(1, kept * 0.25):
            break                      # прочитали уверенно, дальше искать нечего

    # Второй проход: узкий набор языков поверх выбранного. Нужен только на
    # смешанном тексте — там смешанный набор путает кириллицу с латиницей.
    all_words = [w for words in lines.values() for w in words]
    refine = narrow_langs(langs, LAST_SCRIPT)
    if refine and refine != langs and mixed_scripts(all_words):
        found, kept, _ = read(refine)
        fixed = merge_readings(lines, found)
        log(f"второй проход «{refine}»: прочитано {kept}, поправлено слов {fixed}")
        all_words = [w for words in lines.values() for w in words]
    heights = sorted(w["y1"] - w["y0"] for w in all_words) or [10]
    median_h = heights[len(heights) // 2]
    # Колонки ищем только там, где строк несколько. На одной строке любой пробел
    # между словами — это сквозной вертикальный «ров» на всю высоту картинки, и
    # строка резалась на куски: часть переводилась, часть оставалась оригиналом.
    # Особенно заметно на иероглифах, где широкие промежутки у знаков препинания.
    min_gap = max(10, int(0.9 * median_h))
    gaps = _column_gaps(all_words, prepared.width, min_gap) if len(lines) > 1 else []

    # Ров на всю высоту картинки — слишком строгое условие: под сеткой карточек
    # почти всегда есть абзац во всю ширину, он перекрывает разом все рвы, и
    # надписи соседних карточек склеивались в одну строку. Поэтому для каждой
    # строки ищем рвы заново, но только по её же полосе — там мешать некому.
    band_cache = {}

    def band_gaps(y0, y1):
        key = (int(y0), int(y1))
        if key not in band_cache:
            # берём не только саму строку, но и пару строк выше и ниже: иначе
            # обычный пробел между словами оказывается «рвом» — над ним и под
            # ним в своей же строке пусто, и строка резалась на куски
            lo, hi = y0 - 2.5 * median_h, y1 + 2.5 * median_h
            band = [w for w in all_words if w["y1"] > lo and w["y0"] < hi]
            other_row = any(min(y1, w["y1"]) <= max(y0, w["y0"]) for w in band)
            # ров в полосе должен быть заметно шире межсловного пробела и
            # отступа от маркера списка, иначе от пункта отрывается его буллит
            band_cache[key] = (_column_gaps(band, prepared.width,
                                            max(min_gap, int(1.5 * median_h)))
                               if len(band) > 1 and other_row else [])
        return band_cache[key]

    # Какой письменности на снимке больше: по ней решается, какие слова из
    # списка вообще могут всплыть.
    page_letters = [c for w in all_words for c in w["text"] if c.isalpha()]
    cyrillic_page = bool(page_letters) and sum(
        1 for c in page_letters if "\u0400" <= c <= "\u04FF") > len(page_letters) / 2

    out = []
    for key, words in lines.items():
        words.sort(key=lambda w: w["x0"])
        height = max(w["y1"] - w["y0"] for w in words)
        row = band_gaps(min(w["y0"] for w in words), max(w["y1"] for w in words))
        segments, current = [], [words[0]]
        for prev, word in zip(words, words[1:]):
            between = (prev["x1"], word["x0"])
            crosses_column = any(between[0] <= gs and ge <= between[1]
                                 for gs, ge in row)
            if crosses_column or word["x0"] - prev["x1"] > max(2.5 * height, 40):
                segments.append(current)
                current = []
            current.append(word)
        segments.append(current)

        for seg in segments:
            seg = fix_lookalikes(_strip_leading_icon(_strip_edge_junk(seg)),
                                 cyrillic_page)
            joined = " ".join(w["text"] for w in seg)
            ordered = list(reversed(seg)) if _is_rtl_text(joined) else seg
            text = re.sub(r"[ \t]+", " ", join_words(ordered)).strip()
            text = BULLET_JUNK.sub("• ", text)
            if not text:
                continue
            box = [min(w["x0"] for w in seg), min(w["y0"] for w in seg),
                   max(w["x1"] for w in seg), max(w["y1"] for w in seg)]
            # Маркер пункта стоит левее самого текста, а переносы внутри пункта
            # выровнены по тексту. Считать выравнивание по маркеру нельзя:
            # строки пункта переставали считаться одной колонкой и пункт рвался.
            text_x0 = box[0]
            if len(seg) > 1 and LIST_MARKER.match(text):
                text_x0 = min(w["x0"] for w in seg[1:])
            out.append({
                "text": text,
                "box": [v / scale for v in box],
                "text_x0": text_x0 / scale,
                "conf": sum(w["conf"] for w in seg) / len(seg),
                "col": key[:2],  # блок/абзац по версии Tesseract
            })
    out.sort(key=lambda l: (l["box"][1], l["box"][0]))
    return out, [(gs / scale, ge / scale) for gs, ge in gaps], langs


# Последний кусок — знаки препинания и буквы «полной ширины»: «，」）». Они тоже
# пишутся без отбивки, и без них склейка ставила пробел перед каждой запятой.
CJK_RANGES = ((0x1100, 0x11FF), (0x3000, 0x303F), (0x3040, 0x30FF), (0x3400, 0x4DBF),
              (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF))


def join_words(words):
    """Склеиваем слова строки в текст.

    Между иероглифами пробела не ставим: в китайском и японском его нет, а
    Tesseract отдаёт их отдельными кусками. С пробелами «支撑杆» превращалось в
    «支撑 杆», а «安装说明» — в «安 明», и переводчик принимал разорванные слова
    за отдельные: заголовок «инструкция по сборке» становился именем «An Ming».
    """
    out = ""
    for word in words:
        text = word["text"]
        if not text:
            continue
        # Пробел убираем, только когда иероглифы с обеих сторон. Иначе
        # «3.» и «松开» слипались в «3.松开», маркер пункта переставал
        # опознаваться (он требует пробела после себя), и соседние пункты
        # склеивались в один абзац.
        if out and not (_is_cjk(out[-1]) and _is_cjk(text[0])):
            out += " "
        out += text
    return out


def _is_cjk(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in CJK_RANGES)


def looks_like_text(text, conf):
    """Отсекаем мусор OCR: рамки, иконки, ASCII-арт («ааааааа», «| | | |»).

    Цифры и знаки валюты мусором не считаются: «29% Off», «Get it by Tue, Aug 25»
    и «5 Year Protection Plan – $39.99» — нормальный текст, который надо переводить.
    Признак мусора другой: нет ни одного настоящего слова либо одна буква
    повторяется почти по всей строке.
    """
    if conf < 45:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 2:                      # «$239.99», «67346» — переводить нечего
        return False

    # В иероглифике и слоговых азбуках «слов из трёх букв» не бывает, и проверка
    # ниже отсекала бы нормальный китайский, японский и корейский текст целиком.
    if sum(1 for ch in text if _is_cjk(ch)) >= 2:
        return True

    if len(letters) >= 8:                     # на коротких строках доля букв не показательна
        counts = {}
        for ch in letters:
            key = ch.lower()
            counts[key] = counts.get(key, 0) + 1
        if max(counts.values()) / len(letters) > 0.45:
            return False

    words = text.split()
    real = [w for w in words if len(w) >= 3 and sum(c.isalpha() for c in w) / len(w) > 0.6]
    if not real:
        # ни одного полноценного слова — оставляем только короткое «or», «OK», «Ok?»
        compact = text.replace(" ", "")
        return len(words) <= 3 and compact and len(letters) / len(compact) > 0.8
    return True


# Маркер пункта списка: «23.», «7)», «•», «— ». Хвост «(?=\s)» нужен, чтобы
# «39.99» и «2024 год» не принимались за номер пункта.
LIST_MARKER = re.compile(r"^\s*(?:\d{1,3}\s*[.)]|[•·▪◦‣*]|[-–—])(?=\s)")

# Кружок буллита Tesseract читает как что угодно: «e», «®», «©», «»», «›», «*».
# Из-за этого пункты списка не опознавались и слипались в одну простыню текста.
# Приводим такое начало строки к нормальному «•» — дальше его увидит LIST_MARKER.
# Одиночную букву считаем буллитом только перед заглавной: «e Selected» — это
# точно кружок, а «a Modern» вполне может быть началом фразы.
# Одиночная скобка, палка или косая — это рамка кнопки или плашки, попавшая в
# распознавание, а не знак препинания: словом такое не бывает никогда.
EDGE_JUNK_TOKEN = re.compile(r"^[|/\\[\](){}<>«»‹›]+$")


def _strip_edge_junk(seg):
    """Убираем рамку кнопки или плашки, прочитанную как скобки по краям строки.

    Выкидывать её из готового текста мало: рамка остаётся в габаритах блока, а
    она вдвое выше самих букв — оценка кегля задирается, и надпись вроде «NEW»
    рисуется втрое крупнее соседей.
    """
    while len(seg) > 1 and EDGE_JUNK_TOKEN.match(seg[0]["text"].strip()):
        seg = seg[1:]
    while len(seg) > 1 and EDGE_JUNK_TOKEN.match(seg[-1]["text"].strip()):
        seg = seg[:-1]
    return seg


BULLET_JUNK = re.compile(r"^\s*(?:[•·▪◦‣*©®°¢£€§†‡»›~^]|[eoO](?=\s+[A-ZА-ЯЁ]))\s+")


# Значок -> общая буква. Слева то, что выглядит одинаково: латинская «B»,
# русская «В», русская «б» и цифра «6» — на экране это почти один рисунок, и
# распознавание выбирает между ними наугад. Приводя слово к этим общим буквам,
# получаем его «форму»: у «Всего» и «Bcero» она одна и та же.
LOOKALIKE = {"а": "a", "е": "e", "ё": "e", "о": "o", "р": "p", "с": "c", "у": "y",
             "х": "x", "к": "k", "м": "m", "н": "h", "т": "t", "в": "b", "б": "b",
             "г": "r", "п": "n", "и": "u", "ш": "w", "з": "3", "ч": "4", "д": "d",
             "л": "l", "ф": "f", "э": "e", "6": "b", "0": "o", "1": "l", "5": "s"}

_ocr_words = {"mtime": None, "index": {}}


def word_shape(word):
    """«Форма» слова: одинаковые с виду знаки сведены к одной букве."""
    return "".join(LOOKALIKE.get(ch, ch) for ch in word.lower())


def ocr_words():
    """Список знакомых слов: {форма: слово}. Перечитывается по времени файла."""
    try:
        mtime = os.path.getmtime(OCR_WORDS_PATH)
    except OSError:
        _ocr_words["mtime"], _ocr_words["index"] = None, {}
        return {}
    if mtime == _ocr_words["mtime"]:
        return _ocr_words["index"]
    index = {}
    try:
        with open(OCR_WORDS_PATH, encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if not word or word.startswith("#") or len(word) < 2:
                    continue          # слишком короткое слово подошло бы ко всему
                index.setdefault(word_shape(word), word)
    except Exception as e:
        print("ocr_words.txt не прочитался:", e)
        index = {}
    _ocr_words["mtime"], _ocr_words["index"] = mtime, index
    log(f"слов в списке распознавания: {len(index)}")
    return index


def _is_cyrillic_word(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters
                                 if "\u0400" <= c <= "\u04FF") > len(letters) / 2


def fix_lookalikes(seg, cyrillic_page):
    """Возвращаем на место слова, спутанные из-за букв-двойников.

    Заменяем, только если прочитанное слово написано не той письменностью, что
    преобладает НА СНИМКЕ. Иначе английское «bud» на английской же странице
    превратилось бы в русское «Вид»: по форме значков они неразличимы, и
    единственное, что их разводит, — язык остального текста. Считать по одной
    строке нельзя: в таблице ячейка «Bcero» целиком латинская, и языка в ней
    не видно вовсе.
    """
    index = ocr_words()
    if not index:
        return seg
    for word in seg:
        text = word["text"]
        head = text[:len(text) - len(text.lstrip("([{|«\"\'"))]
        core = text[len(head):]
        tail = ""
        while core and not core[-1].isalnum():
            core, tail = core[:-1], core[-1] + tail
        if len(core) < 2:
            continue
        known = index.get(word_shape(core))
        if not known or known.lower() == core.lower():
            continue
        if _is_cyrillic_word(known) != cyrillic_page:
            continue                       # подсказка не на языке снимка
        if _is_cyrillic_word(core) == cyrillic_page:
            continue                       # прочитано и так на нужной письменности
        word["text"] = head + known + tail
    return seg


def _strip_leading_icon(seg):
    """Убираем значок, прочитанный как одна-две буквы: «@ 1 Year Warranty».

    Иконка стоит в одной строке с надписью, и Tesseract читает её как случайные
    знаки — в переводе получается «ГБ Бесплатная доставка» и «©2 Добавить в
    список желаний», — а сам значок ещё и закрашивается заливкой блока. От
    настоящего короткого слова и от буллита списка значок отличают три признака,
    все меряются по самой строке: низкая уверенность распознавания, рост выше
    любой буквы и отрыв от текста шире, чем слова стоят друг от друга.
    """
    # Значок может распасться на два «слова» («С» и «О» от контурного щита),
    # поэтому пробуем дважды. Больше двух не трогаем: дальше начинается текст.
    for _ in range(2):
        if len(seg) < 2:
            break
        head, rest = seg[0], seg[1:]
        text = head["text"].strip()
        if len(text) > 2 or LIST_MARKER.match(text + " "):
            break                     # настоящее слово или маркер пункта списка
        tall = max(w["y1"] - w["y0"] for w in rest)
        gaps = sorted(b["x0"] - a["x1"] for a, b in zip(rest, rest[1:]))
        typical = gaps[len(gaps) // 2] if gaps else 0.25 * tall
        gap = rest[0]["x0"] - head["x1"]
        # Уверенность — самый надёжный признак: настоящее короткое слово («In»,
        # «A») Tesseract читает уверенно, а значок он не читает вовсе и выдаёт
        # первое похожее — «©2», «19», «м’», «\\'» — с низкой оценкой.
        unsure = head["conf"] < 65
        taller = head["y1"] - head["y0"] > 1.3 * tall
        far = gap > max(2.0 * max(1.0, typical), 0.4 * tall)
        if not (unsure or taller or far):
            break
        seg = rest
    return seg


# Конец предложения. Если строка на них не заканчивается, а следующая начинается
# со строчной — это перенос внутри одного абзаца, а не новый абзац.
SENTENCE_END = (".", "!", "?", "…", ":", ";", "。", "！", "？", "：", "；")


def _is_continuation(prev_text, text):
    """Строка продолжает предыдущую, а не начинает новую мысль."""
    prev_text, text = prev_text.rstrip(), text.lstrip()
    if not prev_text or not text:
        return False
    if LIST_MARKER.match(text):
        return False
    starts_lower = text[0].islower()
    return not prev_text.endswith(SENTENCE_END) or starts_lower


# Буквы, уходящие выше x-height и ниже базовой линии. Нужны, чтобы по рамке
# строки угадать кегль: Tesseract обводит сами буквы, а не кегельную площадку.
_TALL = (set("bdfhijklt0123456789([{)]}|@#$&/\\!?%") |
         set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") |
         set("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ") |
         set("бёйф"))              # из строчных кириллических вверх лезут эти
_DEEP = set("gjpqy([{)]}|@$,;/") | set("друфцщ")


def _ink_ratio(text):
    """Какую долю кегля занимает чернильная рамка строки.

    «Черная Тень» — только заглавные и строчные без хвостов, рамка выходит
    заметно ниже, чем у «Рюкзак для мальчиков», хотя шрифт тот же. Если брать
    рамку как есть, перевод такой строки рисуется мельче оригинала — это и была
    вечная «мелкота» на картинке. Поправляем по составу букв: сверху либо
    выносная (примерно 0.75 кегля), либо только x-height (0.52), снизу либо
    хвост (0.21), либо ничего.
    """
    top = 0.52
    bottom = 0.0
    for ch in text:
        if ch in _TALL:
            top = 0.75
        if ch in _DEEP:
            bottom = 0.21
    # Пол на всякий случай: если рамка строки почему-то выше самих букв, делить
    # её на 0.52 — значит получить вдвое завышенный кегль.
    return max(0.62, top + bottom)


def _font_guess(text, h, w):
    """Кегль оригинала по рамке строки.

    Считаем по высоте с поправкой на выносные буквы, но не больше, чем
    позволяет ширина: средняя буква занимает около половины кегля. Проверка по
    ширине нужна потому, что на коротких надписях Tesseract нет-нет да и отдаёт
    рамку заметно выше самих букв — и одна такая надпись рисовалась вдвое
    крупнее соседей по сетке.
    """
    letters = max(1, len(text.strip()))
    return min(h / _ink_ratio(text), (w / (0.5 * letters)) * 1.1)


def _mark_cells(lines):
    """Отмечаем строки, стоящие в строке таблицы: «Бренд» и рядом «YATINEY».

    Признак — сосед на той же высоте через широкий провал, причём левая ячейка
    кончается задолго до середины кадра. Так таблица отличается от текста в две
    колонки (там строки заполняют колонку целиком) и от подписи со временем в
    углу сообщения (там левая часть длинная).

    Ячейки нельзя склеивать со строками сверху и снизу: иначе «Цвет»,
    «Материал» и «Размер» сливаются в одну фразу, значения из разных строк — в
    другую, и вместо характеристик получается каша.
    """
    for line in lines:
        line["cell"] = False
    for line in lines:
        x0, y0, x1, y1 = line["box"]
        h = max(1.0, y1 - y0)
        for other in lines:
            if other is line:
                continue
            ox0, oy0, ox1, oy1 = other["box"]
            if min(y1, oy1) - max(y0, oy0) < 0.5 * min(h, max(1.0, oy1 - oy0)):
                continue                       # не на одной высоте — не соседи
            left, right = (line, other) if x0 <= ox0 else (other, line)
            gap = right["box"][0] - left["box"][2]
            # Ячейка занимает лишь малую часть своей колонки («Бренд» и дальше
            # пусто до значения). В тексте, набранном в две колонки, строка,
            # наоборот, заполняет колонку целиком — по этому и различаем.
            span = max(1.0, right["box"][0] - left["box"][0])
            fill = (left["box"][2] - left["box"][0]) / span
            wide = gap >= 3 * h and fill <= 0.6
            # «Количество в упаковке: 4» — подпись с двоеточием и значение
            # рядом: тоже строка таблицы, даже если провал между ними невелик
            labelled = gap >= 1.5 * h and left["text"].rstrip().endswith(":")
            if wide or labelled:
                line["cell"] = other["cell"] = True
    return lines


def _mark_reach(lines):
    """Отмечаем строки, дотянувшиеся до правого края своей колонки.

    Перенос внутри абзаца — это всегда строка, упёршаяся в правый край: слово
    не поместилось и уехало вниз. Кончилась заметно раньше — значит её оборвали
    намеренно, и это конец абзаца или пункта списка.

    Без этого признака не обойтись: кружок «•» Tesseract часто не видит вовсе,
    а текст пунктов начинается ровно с той же позиции, что и переносы внутри
    пункта. Отличить их по зазору или выравниванию нельзя — только по тому,
    добежала строка до края или нет.
    """
    for i, line in enumerate(lines):
        x0, y0, x1, y1 = line["box"]
        h = max(1.0, y1 - y0)
        # Соседи по колонке: рядом сверху/снизу и с тем же левым краем. Своей
        # считаем строку, выровненную хоть по тексту, хоть по маркеру списка:
        # переносы в списках бывают и так, и так.
        band = [o for o in lines[max(0, i - 4):i + 5]
                if (abs(o["text_x0"] - line["text_x0"]) <= 1.5 * h
                    or abs(o["box"][0] - x0) <= 1.5 * h)
                and abs(o["box"][1] - y0) <= 6 * h]
        right = max(o["box"][2] for o in band)
        # следующая строка той же колонки, а не просто следующая по порядку:
        # в двух колонках соседом по порядку идёт строка из другой колонки
        below = [o for o in band if o["box"][1] > y0 + 0.5 * h]
        nxt = min(below, key=lambda o: o["box"][1]) if below else None
        words = nxt["text"].split() if nxt else []
        if len(band) < 2 or not words:
            line["reaches"] = True
            continue
        # Мерка не «столько-то высот строки», а первое слово следующей строки:
        # перенос случается тогда и только тогда, когда оно в остаток не влезло.
        # Влезало бы — значит строку оборвали намеренно, это конец абзаца.
        char_w = (x1 - x0) / max(1, len(line["text"]))
        line["reaches"] = right - x1 < max((len(words[0]) + 1) * char_w, 1.5 * h)
        line["step"] = nxt["box"][1] - y0        # шаг до следующей строки колонки
    return lines


def _typical_step(lines):
    """Обычный шаг строк внутри абзаца в этом тексте.

    Нужен, чтобы поймать отбивку между пунктами списка: она обычно всего на
    треть больше обычного междустрочного, и абсолютным порогом (в высотах
    буквы) её не отличить — у разных шрифтов и масштабов он попадает то туда,
    то сюда. А вот относительно шага в этом же тексте разница видна сразу.
    """
    # Берём нижнюю четверть, а не медиану: медиану тянут вверх сами отбивки,
    # а нам нужен шаг именно внутри абзаца — с ним и сравниваем.
    steps = sorted(l["step"] for l in lines if l.get("step"))
    return steps[len(steps) // 4] if steps else 0


def _group_paragraphs(lines, typical):
    """Склеиваем строки в абзацы: одна колонка, общий блок, маленький зазор."""
    groups = []
    for line in lines:
        x0, y0, x1, y1 = line["box"]
        h = y1 - y0
        merged = False
        # пункт списка всегда начинает новый абзац: иначе «25. …» и «26. …»
        # слипаются в одно предложение — ломается и вёрстка, и перевод
        if LIST_MARKER.match(line["text"]) or line["cell"]:
            groups.append({"lines": [line["text"]], "box": [x0, y0, x1, y1],
                           "h_sum": h, "font_sum": _font_guess(line["text"], h, x1 - x0),
                           "conf": line["conf"], "col": line["col"],
                           "cell": line["cell"], "reaches": line["reaches"],
                           "last_y0": y0, "text_x0": line["text_x0"]})
            continue
        # в многоколоночной вёрстке строки идут вперемежку по колонкам,
        # поэтому кандидата ищем среди всех недавних групп, а не только последней
        for prev in reversed(groups[-40:]):
            if prev["cell"] or not prev["reaches"]:
                # к ячейке таблицы ничего не приклеиваем, а строку, оборванную
                # до края колонки, следующая не продолжает: это конец абзаца
                continue
            if typical and y0 - prev["last_y0"] > 1.35 * typical:
                continue                 # шаг больше обычного — это отбивка
            px0, py0, px1, py1 = prev["box"]
            avg_h = prev["h_sum"] / len(prev["lines"])
            gap = y0 - py1
            # Перенос внутри предложения терпит зазор побольше: строки одного
            # абзаца иногда стоят просторнее, чем «половина высоты буквы».
            goes_on = _is_continuation(prev["lines"][-1], line["text"])
            if gap > (1.3 if goes_on else 0.9) * avg_h:
                continue
            overlap = min(x1, px1) - max(x0, px0)
            same_column = overlap > 0.55 * min(x1 - x0, px1 - px0)
            # Перенос выравнивают двумя способами: под текстом пункта (списки
            # с буллитом) и под самим маркером (нумерованный текст в чате).
            # Годится любой из них, иначе половина списков рвётся на строки.
            tol = max(1.5 * avg_h, 12)
            aligned = (abs(line["text_x0"] - prev["text_x0"]) < tol
                       or abs(x0 - px0) < tol)
            # На совпадение блоков Tesseract полагаться нельзя: при чуть большем
            # межстрочном он разносит строки одного абзаца по разным блокам, и
            # предложение переводилось двумя кусками — с ломаной грамматикой.
            if ((prev["col"] == line["col"] or goes_on) and same_column and aligned
                    and gap >= -0.3 * avg_h):
                prev["lines"].append(line["text"])
                prev["reaches"] = line["reaches"]
                prev["last_y0"] = y0
                prev["h_sum"] += h
                prev["font_sum"] += _font_guess(line["text"], h, x1 - x0)
                prev["conf"] = (prev["conf"] * (len(prev["lines"]) - 1)
                                + line["conf"]) / len(prev["lines"])
                prev["box"] = [min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1)]
                merged = True
                break
        if not merged:
            groups.append({"lines": [line["text"]], "box": [x0, y0, x1, y1],
                           "h_sum": h, "font_sum": _font_guess(line["text"], h, x1 - x0),
                           "conf": line["conf"], "col": line["col"],
                           "cell": False, "reaches": line["reaches"], "last_y0": y0,
                           "text_x0": line["text_x0"]})
    return groups


def ocr_blocks(img):
    """Распознаём область и возвращаем (полный_текст, [абзацы с координатами])."""
    lines, gaps, langs = _ocr_lines(img)
    lines = _mark_reach(_mark_cells(lines))
    step = _typical_step(lines)          # обычный шаг строк внутри абзаца
    blocks, skipped = [], 0
    for g in _group_paragraphs(lines, step):
        text = " ".join(g["lines"]).strip()
        if not text:
            continue
        # Ячейку таблицы с числом («4», «36" x 39" x 87"») фильтр мусора режет:
        # переводить там нечего. Но рядом стоит её подпись, и без значения
        # строка выглядит оборванной, поэтому ячейки оставляем как есть.
        if not looks_like_text(text, g["conf"]) and not (g["cell"] and g["conf"] >= 60):
            skipped += 1
            continue
        x0, y0, x1, y1 = g["box"]
        pad = 2
        # правый край колонки: перевод длиннее оригинала, но за границу
        # карточки/колонки вылезать не должен
        col_right = min([gs for gs, _ in gaps if gs >= x1 - 1] or [img.width])
        blocks.append({
            "text": text,
            "bbox": (max(0, int(x0) - pad), max(0, int(y0) - pad),
                     min(img.width, int(x1) + pad + 1), min(img.height, int(y1) + pad)),
            "lines": len(g["lines"]),
            "line_h": g["h_sum"] / len(g["lines"]),
            "font_h": g["font_sum"] / len(g["lines"]),   # кегль оригинала
            "line_step": step,
            "col_right": int(col_right),
        })
    blocks = reading_order(blocks)
    log(f"OCR: строк {len(lines)}, блоков {len(blocks)}, отброшено мусора {skipped}")
    return join_blocks(blocks), blocks, langs


def reading_order(blocks):
    """Блоки в порядке чтения: строками сверху вниз, внутри строки слева направо.

    Сортировки по (верх, лево) на таблице не хватает: ячейки одной строки стоят
    на пиксель-другой выше или ниже соседних, и «шт.» из одной строки
    оказывалось между названиями из разных — текст оригинала переставал
    совпадать с картинкой. Поэтому сначала собираем блоки в полосы по
    вертикальному перекрытию, а уже внутри полосы сортируем слева направо.
    """
    rows = []
    for block in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])):
        y0, y1 = block["bbox"][1], block["bbox"][3]
        for row in rows:
            overlap = min(y1, row["bottom"]) - max(y0, row["top"])
            if overlap > 0.5 * min(y1 - y0, row["bottom"] - row["top"]):
                row["items"].append(block)
                row["top"] = min(row["top"], y0)
                row["bottom"] = max(row["bottom"], y1)
                break
        else:
            rows.append({"top": y0, "bottom": y1, "items": [block]})
    out = []
    for row in rows:
        out.extend(sorted(row["items"], key=lambda b: b["bbox"][0]))
    return out


def join_blocks(blocks, texts=None):
    """Собираем абзацы в один текст, повторяя раскладку оригинала.

    Пункт списка — всегда отдельный блок, даже когда стоит вплотную к
    предыдущему. Разделять всё подряд пустой строкой поэтому нельзя: список
    из десяти пунктов растянулся бы вдвое против того, как он выглядел на
    экране. Смотрим на зазор между блоками: строки шли подряд — просто
    перенос, была отбивка или соседняя колонка — пустая строка.
    """
    if texts is None:
        texts = [b["text"] for b in blocks]
    if len(blocks) < 2:
        return "".join(texts)
    line_h = max(1.0, sorted(b["line_h"] for b in blocks)[len(blocks) // 2])
    gaps = [blocks[i]["bbox"][1] - blocks[i - 1]["bbox"][3]
            for i in range(1, len(blocks))]
    # Мерка — обычный шаг строк внутри абзаца в этом же снимке. Блоки, идущие
    # с таким же шагом, стояли просто на соседних строках: между ними перенос.
    # Всё, что просторнее, на экране и выглядело отдельными кусками — там
    # пустая строка. Абсолютный порог тут не работает: рамка строки то с
    # выносными буквами, то без, и один и тот же зазор гуляет в полтора раза.
    step = blocks[0].get("line_step") or 0
    cut = 0.5 * step if step else 0.85 * line_h
    out = [texts[0]]
    for i, gap in enumerate(gaps, 1):
        prev, b = blocks[i - 1], blocks[i]
        # Однострочные блоки на одной высоте — это строка таблицы: «Бренд» и
        # «YATINEY» стояли рядом, значит и в тексте им место в одной строке, а
        # не в разных, как получалось раньше.
        same_row = (prev["lines"] == 1 and b["lines"] == 1
                    and b["bbox"][0] >= prev["bbox"][2]
                    and min(b["bbox"][3], prev["bbox"][3])
                    - max(b["bbox"][1], prev["bbox"][1]) > 0.5 * line_h)
        if same_row:
            out.append(" " if texts[i - 1].rstrip().endswith(":") else " — ")
        else:
            # отрицательный зазор — блок начался выше предыдущего, то есть это
            # соседняя колонка: там пустая строка как раз к месту
            out.append("\n" if 0 <= gap <= cut else "\n\n")
        out.append(texts[i])
    return "".join(out)


# --------------------------------------------------------------------------------------
#  Перевод
# --------------------------------------------------------------------------------------
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _split_chunks(text, limit=1400):
    """Режем длинный текст по абзацам/предложениям — у бесплатного endpoint есть лимит."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for part in re.split(r"(\n\n|(?<=[.!?…])\s+)", text):
        if part is None:
            continue
        if len(cur) + len(part) > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += part
    if cur.strip():
        chunks.append(cur)
    return chunks


def _google_translate(text, target, source="auto"):
    out, detected = [], source
    for chunk in _split_chunks(text):
        r = requests.post(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source, "tl": target, "dt": "t"},
            data={"q": chunk}, headers=UA, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        out.append("".join(seg[0] for seg in data[0] if seg and seg[0]))
        if len(data) > 2 and data[2]:
            detected = data[2]
    return "".join(out), detected


def _google_chrome_translate(text, target, source="auto"):
    """Вторая бесплатная точка Google — та, куда ходит переводчик в Chrome.

    Нужна, когда основная отвечает 429 «слишком много запросов»: счётчики у них
    разные, и вторая обычно ещё жива. Перевод чуть грубее, зато отвечает за
    доли секунды — против нескольких секунд у запасного сервиса.
    """
    out, detected = [], source
    for chunk in _split_chunks(text):
        r = requests.get("https://clients5.google.com/translate_a/t",
                         params={"client": "dict-chrome-ex", "sl": source,
                                 "tl": target, "q": chunk},
                         headers=UA, timeout=15)
        r.raise_for_status()
        data = r.json()
        # ответ бывает двух видов: [["перевод", "en"]] и просто "перевод"
        item = data[0] if isinstance(data, list) and data else data
        if isinstance(item, list):
            out.append(str(item[0]))
            if len(item) > 1 and item[1]:
                detected = str(item[1])
        else:
            out.append(str(item))
    return "".join(out), detected


# У DeepL коды языков заглавными, и для нескольких языков нужен вариант.
_DEEPL_TARGETS = {"en": "EN-US", "pt": "PT-PT", "zh": "ZH", "zh-cn": "ZH", "zh-tw": "ZH"}


def _deepl_lang(iso):
    iso = (iso or "ru").lower()
    return _DEEPL_TARGETS.get(iso, iso.upper())


def _deepl_translate(text, target, key):
    """Официальный DeepL по ключу пользователя.

    Бесплатный ключ оканчивается на «:fx» и ходит на другой адрес — если
    перепутать, сервис отвечает «ключ не подходит», хотя ключ верный.
    """
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    out, detected = [], ""
    for chunk in _split_chunks(text, 4000):
        r = requests.post(f"https://{host}/v2/translate",
                          headers={"Authorization": f"DeepL-Auth-Key {key}", **UA},
                          data={"text": chunk, "target_lang": _deepl_lang(target)},
                          timeout=20)
        if r.status_code in (401, 403):
            raise RuntimeError("DeepL не принял ключ — проверьте его в личном кабинете")
        if r.status_code == 456:
            raise RuntimeError("на ключе DeepL закончился месячный объём")
        r.raise_for_status()
        parts = r.json()["translations"]
        out.append("".join(p["text"] for p in parts))
        if parts:
            detected = (parts[0].get("detected_source_language") or detected).lower()
    return "".join(out), detected


def _google_cloud_translate(text, target, key):
    """Официальный Google Cloud Translation по ключу пользователя."""
    out, detected = [], ""
    for chunk in _split_chunks(text, 4000):
        r = requests.post("https://translation.googleapis.com/language/translate/v2",
                          params={"key": key},
                          data={"q": chunk, "target": target, "format": "text"},
                          headers=UA, timeout=20)
        if r.status_code in (400, 401, 403):
            raise RuntimeError("Google Cloud не принял ключ — проверьте его в консоли")
        r.raise_for_status()
        item = r.json()["data"]["translations"][0]
        # даже с format=text сервис возвращает «&#39;» вместо апострофа
        out.append(html.unescape(item.get("translatedText", "")))
        detected = item.get("detectedSourceLanguage", detected)
    return "".join(out), detected


def _mymemory_translate(text, target, source="en"):
    """Запасной переводчик, если Google недоступен."""
    out = []
    for chunk in _split_chunks(text, 480):
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": chunk, "langpair": f"{source}|{target}"},
                         headers=UA, timeout=15)
        r.raise_for_status()
        out.append(r.json()["responseData"]["translatedText"])
    return " ".join(out), source


_GLOSSARY = {"mtime": None, "rules": []}


def glossary_rules():
    """Правила из glossary.json: {"как перевёл движок": "как надо"}.

    Файл перечитывается на лету по времени изменения — правку словаря видно
    сразу, перезапускать программу не нужно.
    """
    try:
        mtime = os.path.getmtime(GLOSSARY_PATH)
    except OSError:
        _GLOSSARY["mtime"], _GLOSSARY["rules"] = None, []
        return []
    if mtime == _GLOSSARY["mtime"]:
        return _GLOSSARY["rules"]
    rules = []
    try:
        with open(GLOSSARY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for src, dst in data.items():
            src = str(src).strip()
            # JSON не умеет комментариев, а пояснение в файле нужно — и автору
            # словаря, и в образце из сборки. Ключи с подчёркивания — заметки,
            # правилами не становятся.
            if src and not src.startswith("_"):
                rules.append((re.compile(re.escape(src), re.IGNORECASE), str(dst)))
        # длинные фразы применяем первыми, иначе их съедят замены покороче
        rules.sort(key=lambda r: -len(r[0].pattern))
    except Exception as e:
        print("glossary.json не прочитался:", e)
        rules = []
    _GLOSSARY["mtime"], _GLOSSARY["rules"] = mtime, rules
    log(f"глоссарий: {len(rules)} правил")
    return rules


def apply_glossary(text):
    """Правим термины, которые машинный перевод стабильно переводит не так."""
    for pattern, dst in glossary_rules():
        def repl(m, dst=dst):
            # «Держатель контракта» в начале строки не должен стать строчным
            return dst[:1].upper() + dst[1:] if m.group(0)[:1].isupper() and dst else dst
        text = pattern.sub(repl, text)
    return text


# Сервис, который только что отказал, столько секунд не трогаем. Иначе каждый
# следующий кусок снова ждёт отказа: на картинке из десяти абзацев это десяток
# лишних запросов впустую — те самые секунды ожидания.
PROVIDER_COOLDOWN = 60
_provider_down = {}

# Один и тот же кусок экрана переводят по нескольку раз: поправил выделение,
# снял заново. Готовый перевод помним — это и мгновенно, и бережёт лимит
# бесплатной точки, из-за которого начинается 429 и переход на запасные.
CACHE_LIMIT = 200
_cache = {}


def translate(text, target=None, source_hint=None):
    target = target or CFG.get("target_lang", "ru")
    text = text.strip()
    if not text:
        return "", ""
    # у запасного сервиса нет автоопределения: берём подсказку от OCR,
    # а если её нет — грубо по алфавиту
    src = source_hint or ("ru" if is_cyrillic(text) else "en")
    # Свой ключ — первым: за него платят, значит ему и доверяем. Бесплатные
    # точки остаются запасом, чтобы разовый сбой сервиса не оставил без перевода.
    service = str(CFG.get("translator", "free") or "free").lower()
    api_key = str(CFG.get("api_key", "") or "").strip()
    providers = []
    if api_key and service == "deepl":
        providers.append(("DeepL", lambda: _deepl_translate(text, target, api_key)))
    elif api_key and service in ("google_api", "google_cloud"):
        providers.append(("Google Cloud",
                          lambda: _google_cloud_translate(text, target, api_key)))
    providers += [
        ("Google", lambda: _google_translate(text, target)),
        ("Google (chrome)", lambda: _google_chrome_translate(text, target)),
        ("MyMemory", lambda: _mymemory_translate(text, target, src)),
    ]
    # в кэше держим сырой ответ сервиса: глоссарий правится на лету, и его
    # правила должны применяться к старым переводам тоже
    # сервис в ключе кэша: после смены переводчика старые ответы не подсовываем
    key = (text, target, service if api_key else "free")
    if key in _cache:
        result, detected = _cache[key]
        return apply_glossary(result), detected

    now = time.monotonic()
    # если на отдыхе сразу все, отдых не соблюдаем: лучше подождать, чем не перевести
    order = [p for p in providers if _provider_down.get(p[0], 0) <= now] or providers

    last = None
    for name, call in order:
        try:
            result, detected = call()
        except Exception as e:
            last = e
            _provider_down[name] = time.monotonic() + PROVIDER_COOLDOWN
            log(f"{name} не ответил: {e}")
            continue
        _provider_down.pop(name, None)
        if name != providers[0][0]:
            log(f"перевод получен через {name}")
        if len(_cache) >= CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (result, detected)
        return apply_glossary(result), detected
    raise RuntimeError(f"Не удалось перевести: {last}")


def is_translatable(text):
    """Есть ли тут что переводить.

    «$42.99», «16 x 12 x 5», «40,7» — не текст, а число: любой переводчик
    вернёт их как есть. Программа считала это неудачей перевода и переспрашивала
    каждый такой кусок отдельным запросом. На карточке товара с двумя десятками
    цен из этого набегало пять секунд ожидания на пустом месте.
    """
    return sum(1 for ch in text if ch.isalpha()) >= 2


def _needs_retranslate(src, dst, target):
    """Похоже, этот кусок не перевёлся, а вернулся как был."""
    if not is_translatable(src):
        return False
    # Исходник уже на языке перевода — вернуть его как был и есть правильный
    # ответ, а не отказ. Без этой проверки русский счёт, переводимый на русский,
    # выглядел как сотня неудач подряд.
    if already_target(src, target):
        return False
    if not dst.strip():
        return True
    if dst.strip() == src.strip():
        return True
    # Для русского и английского можно проверить прямо: результат должен быть на
    # целевом языке. Для остальных такой проверки нет — довольствуемся
    # сравнением «вернулось как было».
    if target in ("ru", "en") and not already_target(dst, target):
        return True
    return False


def translate_many(texts, target=None, source_hint=None):
    """Переводим список абзацев одним запросом, разделяя их редким маркером.

    Пакетом дёшево — один запрос вместо десятка, — но у него есть изъян: сервис
    определяет ОДИН исходный язык на весь пакет. Если на картинке языки смешаны,
    куски на прочих языках возвращаются нетронутыми. Поэтому после пакета
    проверяем каждый кусок и непереведённые до-переводим по одному: тогда у
    каждого своё автоопределение языка.
    """
    target = target or CFG.get("target_lang", "ru")
    texts = list(texts)
    if not texts:
        return []

    # Куски, уже написанные на языке перевода, не отправляем вовсе. На русском
    # счёте, переводимом на русский, это почти вся таблица: сервис возвращал их
    # без изменений, программа считала это неудачей и переспрашивала каждый
    # отдельным запросом — сотня запросов и полминуты ожидания на ровном месте.
    todo = [i for i, t in enumerate(texts) if not already_target(t, target)]
    if not todo:
        log(f"переводить нечего: все {len(texts)} кусков уже на «{target}»")
        return list(texts)
    if len(todo) < len(texts):
        log(f"уже на «{target}»: {len(texts) - len(todo)} из {len(texts)}, не трогаем")
        done = list(texts)
        for i, value in zip(todo, translate_many([texts[i] for i in todo],
                                                 target, source_hint)):
            done[i] = value
        return done

    if len(texts) == 1:
        return [translate(texts[0], target, source_hint)[0]]

    parts = None
    try:
        result, _ = translate("\n@@@\n".join(texts), target, source_hint)
        split = [p.strip() for p in re.split(r"\s*@\s*@\s*@\s*", result)]
        if len(split) == len(texts):
            parts = split
    except Exception as e:
        log("пакетный перевод не удался:", e)

    if parts is None:                      # маркер не пережил перевод
        parts = list(texts)

    retry = [i for i, (src, dst) in enumerate(zip(texts, parts))
             if _needs_retranslate(src, dst, target)]
    if retry:
        log(f"до-переводим по одному: {len(retry)} из {len(texts)}")

        def one(i):
            try:
                return i, translate(texts[i], target, source_hint)[0]
            except Exception as e:
                log(f"кусок {i} не перевёлся:", e)
                return i, parts[i]

        # Разом, а не по очереди. Каждый такой запрос идёт треть секунды, и на
        # счёте из сотни строк набегало четырнадцать секунд ожидания — притом
        # что почти все эти куски (имена деталей, артикулы) возвращаются как
        # были. Больше четырёх запросов сразу бесплатной точке перевода не
        # нравится: она отвечает «слишком часто» и уходит в отказ.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, value in pool.map(one, retry):
                parts[i] = value
    return parts


def already_target(text, target):
    """Этот кусок уже на нужном языке — трогать не надо."""
    if target == "ru":
        return is_russian(text)
    if target == "en":
        letters = [c for c in text if c.isalpha()]
        return bool(letters) and all(c.isascii() for c in letters)
    return False


# Кириллицей пишут не только по-русски. Казахское «Сәлеметсіз бе!» — кириллица,
# но переводить его надо. Отличаем по буквам, которых в русском алфавите нет:
# ә, ғ, қ, ң, ө, ұ, ү, һ, і (казахский), і, ї, є, ґ (украинский), ў (белорусский).
RU_ALPHABET = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def is_russian(text):
    """Текст кириллический и при этом без букв, чужих русскому алфавиту."""
    letters = [c.lower() for c in text if c.isalpha()]
    if not letters:
        return False
    cyr = [c for c in letters if "\u0400" <= c <= "\u04FF"]
    if len(cyr) < len(letters) * 0.6:
        return False
    return all(c in RU_ALPHABET for c in cyr)


def is_cyrillic(text, threshold=0.6):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    return cyr / len(letters) >= threshold


# --------------------------------------------------------------------------------------
#  Отрисовка перевода поверх картинки (режим "как Google Translate")
# --------------------------------------------------------------------------------------
FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_CANDIDATES = ["segoeui.ttf", "arial.ttf", "tahoma.ttf", "calibri.ttf"]

# Каждой письменности — свой шрифт. Segoe UI знает латиницу, кириллицу, греческий,
# иврит и арабицу, но ни деванагари, ни корейского, ни иероглифов в нём нет: вместо
# букв рисовались пустые квадратики. Нужные шрифты есть в любой Windows.
SCRIPT_FONTS = [
    (((0x0900, 0x0DFF),), ["Nirmala.ttf"]),                  # индийские письменности
    (((0x0E00, 0x0E7F),), ["LeelawUI.ttf", "tahoma.ttf"]),   # тайская
    (((0x0E80, 0x0EFF),), ["LaoUI.ttf", "LeelawUI.ttf"]),    # лаосская
    (((0x1000, 0x109F),), ["mmrtext.ttf"]),                  # бирманская
    (((0x1780, 0x17FF),), ["khmerui.ttf", "LeelawUI.ttf"]),  # кхмерская
    (((0x10A0, 0x10FF), (0x0530, 0x058F)), ["sylfaen.ttf"]),  # грузинская, армянская
    (((0x1200, 0x137F),), ["ebrima.ttf", "nyala.ttf"]),      # эфиопская
    (((0x3040, 0x30FF),), ["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc"]),   # кана
    (((0xAC00, 0xD7AF), (0x1100, 0x11FF)), ["malgun.ttf"]),  # хангыль
    (((0x4E00, 0x9FFF), (0x3400, 0x4DBF)), ["msyh.ttc", "simsun.ttc"]),     # иероглифы
]

_font_cache = {}


def _font_file(text):
    """Файл шрифта под письменность текста; None — подойдёт обычный.

    Считаем буквы каждой письменности и берём ту, которых больше: перевод
    почти всегда однороден, а случайный иероглиф в латинской строке не должен
    утаскивать весь блок в другой шрифт. Кана и хангыль проверяются раньше
    иероглифов — японский и корейский свои шрифты рисуют красивее китайского.
    """
    # Иероглиф не говорит, японский он или китайский: «画像» одинаково пишется и
    # там, и там, а рисуются они по-разному. Спрашиваем у языка перевода.
    lang = str(CFG.get("target_lang", "") or "").lower()
    hint = {"ja": ["YuGothM.ttc", "meiryo.ttc"], "ko": ["malgun.ttf"],
            "zh": ["msyh.ttc"]}.get(lang.split("-")[0])
    if hint and any(_is_cjk(ch) for ch in text):
        for name in hint:
            path = os.path.join(FONTS_DIR, name)
            if os.path.isfile(path):
                return path

    best, best_n = None, 0
    for ranges, files in SCRIPT_FONTS:
        n = sum(1 for ch in text if any(a <= ord(ch) <= b for a, b in ranges))
        if n > best_n:
            best, best_n = files, n
    for name in (best or []) + FONT_CANDIDATES:
        path = os.path.join(FONTS_DIR, name)
        if os.path.isfile(path):
            return path
    return None


def _font(size, text=""):
    path = _font_file(text)
    key = (path, size)
    if key not in _font_cache:
        try:
            _font_cache[key] = (ImageFont.truetype(path, size) if path
                                else ImageFont.load_default())
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _wrap(draw, text, font, max_width):
    lines, cur = [], ""
    for word in text.split():
        probe = (cur + " " + word).strip()
        if draw.textlength(probe, font=font) <= max_width or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _size_bands(plans, spread=1.12):
    """Блоки, разбитые по кеглю оригинала: подписи, тело, подзаголовки, заголовки.

    Равнять кегль имеет смысл только внутри такой группы. Пока равняли всё
    подряд, мелкая подпись под значком тянула вниз и основной текст, и
    заголовки: каждый блок помещался в свой родной размер, но всем всё равно
    назначался кегль нижней шестой части — перевод выходил заметно мельче
    оригинала на ровном месте.
    """
    bands, current = [], []
    for plan in sorted(plans, key=lambda p: p["start"]):
        if current and plan["start"] > current[0]["start"] * spread:
            bands.append(current)
            current = []
        current.append(plan)
    if current:
        bands.append(current)
    return bands


def _background_of(region, tol=60):
    """Цвет фона под надписью и ровный ли он. Возвращает (цвет, ровный).

    Считаем по рамке области, а не по всей: буквы стоят внутри, а по краю почти
    всегда фон. Так решаются сразу две задачи.

    Цвет: у плотно обведённой белой надписи на синей кнопке самым частым цветом
    внутри рамки оказывались сами буквы, и плашка закрашивалась белым.

    Ровность: у надписи на ровном фоне рамка одноцветная (сто из ста), у надписи
    на градиенте — нет (около семидесяти). Долю фона по всей области считать
    нельзя, её у плотной надписи мало; число цветов — тоже: текст на белом давал
    74–85%, впритык к любому порогу, и часть надписей уходила под размытие,
    оставляя на странице серую плашку ростом с оригинал.
    """
    width, height = region.size
    if width < 3 or height < 3:
        return dominant_color(region), True
    px = region.convert("RGB").load()
    edge = []
    for x in range(0, width, max(1, width // 64)):
        edge.append(px[x, 0])
        edge.append(px[x, height - 1])
    for y in range(0, height, max(1, height // 32)):
        edge.append(px[0, y])
        edge.append(px[width - 1, y])
    # Голосуем огрублёнными цветами: сглаживание букв иначе дробит голоса.
    votes = {}
    for c in edge:
        key = (c[0] // 16, c[1] // 16, c[2] // 16)
        votes[key] = votes.get(key, 0) + 1
    winner = max(votes.items(), key=lambda kv: kv[1])[0]
    same = [c for c in edge if (c[0] // 16, c[1] // 16, c[2] // 16) == winner]
    bg = tuple(sum(c[i] for c in same) // len(same) for i in range(3))
    near = sum(1 for c in edge
               if abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2]) <= tol)
    return bg, near >= 0.85 * len(edge)


def _fit_text(draw, text, box_w, box_h, start_size, min_size=8, spacing=1.25):
    """Максимальный кегль, при котором перевод влезает в блок → (size, lines, line_h).

    На минимальном размере функция отдаёт то, что получилось, даже если оно не
    влезло. Проверять это должен вызывающий: раньше не влезший хвост молча
    уезжал на соседний блок и наползал на чужой текст.
    """
    size = max(min_size, min(int(start_size), 200))
    while True:
        font = _font(size, text)
        lines = _wrap(draw, text, font, box_w)
        line_h = max(1, int(size * spacing))
        # Ширину проверяем отдельно от высоты: перенос спасает не всегда.
        # «Водонепроницаемость» — одно слово, разорвать его негде, и по числу
        # строк оно всегда «влезало», а на картинке уезжало в соседнюю колонку
        # поверх чужого текста. Единственный способ вместить такое — уменьшить.
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        if (len(lines) * line_h <= box_h and widest <= box_w) or size <= min_size:
            return size, lines, line_h
        size -= 1


def dominant_color(region):
    """Самый частый цвет области — это фон под текстом (тёмная тема, светлая, любая)."""
    small = region.resize((max(1, min(60, region.width)), max(1, min(30, region.height))))
    quantized = small.quantize(colors=8).convert("RGB")
    colors = quantized.getcolors(maxcolors=100000)
    if not colors:
        return (255, 255, 255)
    return max(colors)[1]


def render_overlay(img, blocks, translations, zoom=None):
    """Закрашиваем исходный текст и пишем поверх перевод.

    `zoom` перебивает настройку `overlay_zoom` — так кнопки 1× / 1.5× / 2×
    в окне результата пересобирают ту же картинку крупнее.

    Четыре прохода, и порядок принципиален:
      1. считаем, сколько места есть у каждого блока и какой кегль он просит;
      2. выбираем один общий кегль на «тело» текста, чтобы шрифт не прыгал;
      3. раскладываем строки этим кеглем, лишнее обрезаем явным «…»;
      4. закрашиваем ВСЕ блоки и только потом пишем ВЕСЬ текст.
    Пункт 4 важен: при отрисовке блок-за-блоком заливка нижнего блока затирала
    хвост текста верхнего, и слово выглядело обрезанным.
    """
    src = img.convert("RGB")

    # Увеличение всего оверлея. Кегль относительно блока при этом не меняется,
    # зато результат становится читаемым при любой вёрстке — в отличие от
    # overlay_font_scale, который упирается в высоту исходного блока.
    # Рисуем сразу в увеличенном разрешении, иначе текст вышел бы мыльным.
    zoom = _clamp(CFG.get("overlay_zoom", 1.0) if zoom is None else zoom, 1.0, 3.0, 1.0)
    if zoom > 1.001:
        src = src.resize((max(1, int(src.width * zoom)), max(1, int(src.height * zoom))),
                         Image.LANCZOS)
        blocks = [dict(b,
                       bbox=tuple(int(v * zoom) for v in b["bbox"]),
                       line_h=b.get("line_h", 10) * zoom,
                       font_h=b.get("font_h", b.get("line_h", 10)) * zoom,
                       col_right=int(b.get("col_right", img.width) * zoom))
                  for b in blocks]

    base_h = src.height

    # У нижних блоков под перевод почти нет места: он длиннее оригинала, а сразу
    # под ним — край выделения. Такой блок ужимался до нечитаемого и тянул за
    # собой общий кегль всей картинки. Наращиваем холст, лишнее срежем в конце.
    pad = min(400, max(48, base_h // 2))
    tail = dominant_color(src.crop((0, max(0, base_h - 8), src.width, base_h)))
    grown = Image.new("RGB", (src.width, base_h + pad), tail)
    grown.paste(src, (0, 0))
    src = grown
    out = src.copy()
    draw = ImageDraw.Draw(out)

    # русский текст обычно длиннее оригинала, поэтому блоку разрешено
    # разрастись вправо и вниз — но не залезая на соседей (колонки, карточки)
    limits = {}
    for idx, block in enumerate(blocks):
        x0, y0, x1, y1 = block["bbox"]
        right, bottom = block.get("col_right", src.width), src.height
        # Блок, уже перенесённый на несколько строк, занял всю ширину своего
        # места — колонки, карточки, ячейки: перенос и случился потому, что
        # правее текста нет. Расти вправо ему нельзя, иначе перевод вылезает за
        # край карточки на пустое поле — сосед справа его не удержит, соседа там
        # нет. Однострочному расти можно: его ширина — это ширина надписи, а не
        # ширина места под неё.
        if block.get("lines", 1) > 1:
            right = min(right, x1)
        # Вниз блок расти может, но не более чем на две своих высоты. Соседа
        # снизу, который бы его остановил, может не оказаться вовсе: на скане
        # мелкий текст под ним нередко не распознаётся, и тогда перевод
        # разрастался на пустое место и затирал таблицу под собой.
        bottom = min(bottom, y1 + 2 * (y1 - y0))
        column_right = x1
        near = 8 * (block.get("line_h") or 12)
        for other_idx, other in enumerate(blocks):
            if other_idx == idx:
                continue
            ox0, oy0, ox1, oy1 = other["bbox"]
            if ox0 >= x1 and min(y1, oy1) > max(y0, oy0):        # сосед справа
                right = min(right, ox0 - 4)
            if oy0 >= y1 and min(x1, ox1) > max(x0, ox0):        # сосед снизу
                bottom = min(bottom, oy0 - 1)
            # Сосед сверху или снизу в той же колонке. У крайней карточки соседа
            # справа нет вовсе, и без этого признака перевод вылезал за её край
            # на пустое поле. Где кончается колонка, показывает самая длинная
            # строка в ней — своя же или соседняя.
            if (min(x1, ox1) - max(x0, ox0) > 0.5 * min(x1 - x0, ox1 - ox0)
                    and abs(oy0 - y0) < near):
                column_right = max(column_right, ox1)
        # Короткой подписи («Вес», «Цвет») колонка соседей всё равно мала —
        # разрешаем ей четверть своей ширины сверх, иначе перевод такой ячейки
        # обрезался бы многоточием на ровном месте.
        right = min(right, max(column_right, x1 + (x1 - x0) // 4))
        limits[idx] = (max(x1, right), max(y1, bottom))

    # настройки шрифта перевода — см. config.json
    scale = _clamp(CFG.get("overlay_font_scale", 1.0), 0.5, 3.0, 1.0)
    min_font = int(_clamp(CFG.get("overlay_min_font", 8), 6, 40, 8))
    spacing = _clamp(CFG.get("overlay_line_spacing", 1.25), 1.0, 2.0, 1.25)
    uniform = bool(CFG.get("overlay_uniform_font", True))

    # --- проход 1: место под блок и запрошенный им кегль
    plans = []
    for idx, (block, translated) in enumerate(zip(blocks, translations)):
        if not translated.strip():
            continue
        x0, y0, x1, y1 = block["bbox"]
        w, h = x1 - x0, y1 - y0
        if w < 8 or h < 6:
            continue
        max_right, max_bottom = limits.get(idx, (src.width, src.height))

        # Ориентир — кегль оригинала, посчитанный с поправкой на выносные
        # буквы. По голой рамке строки он выходил тем меньше, чем «ровнее»
        # набрана строка, и перевод почти всегда получался мельче исходного.
        own_h = block.get("font_h") or block.get("line_h",
                                                 h / max(1, block.get("lines", 1)))
        # Без запаса вниз: оценка кегля и так выходит на пару пунктов меньше
        # исходной (Tesseract обводит буквы, а не кегельную площадку), и лишний
        # коэффициент делал перевод мельче оригинала уже на этом шаге. Если
        # перевод не влезет — его ужмёт _fit_text, для того он и есть.
        start_size = max(min_font, int(round(own_h * scale)))
        avail_w = max(40, max_right - x0 - 2)
        avail_h = max(h, max_bottom - y0)
        plans.append({"box": (x0, y0, x1, y1), "text": translated, "start": start_size,
                      "avail": (avail_w, avail_h), "limit": (max_right, max_bottom),
                      "size": start_size})
    if not plans:
        return out.crop((0, 0, out.width, base_h))

    # --- проход 2: кегль по классам текста.
    # Блоки разбиты по кеглю оригинала: подписи, тело, заголовки. Общий кегль на
    # всех означал, что самый тесный блок мельчит весь снимок, — поэтому классы
    # считаются порознь.
    for band in _size_bands(plans):
        # Внутри класса равняемся на его медиану, а не на собственную оценку
        # блока. Кегль оригинала берётся из рамки строки, и на коротких словах
        # без выносных букв («Вес», «Модель») оценка пляшет на пару пунктов —
        # соседние строки таблицы от этого прыгали.
        target = band[len(band) // 2]["start"]             # band отсортирован
        for p in band:
            avail_w, avail_h = p["avail"]
            # Медиана класса поправляет заниженную оценку, но поднимать блок
            # заметно выше его собственного размера нельзя: абзац, попавший в
            # один класс с заголовком, рисовался вдвое крупнее оригинала и
            # накрывал собой то, что под ним.
            start = min(target, int(p["start"] * 1.25)) if uniform else p["start"]
            p["size"] = _fit_text(draw, p["text"], avail_w, avail_h,
                                  max(min_font, start), min_font, spacing)[0]
        if uniform:
            # Равняемся не на самый тесный блок, а на нижнюю шестую часть: одна
            # тесная карточка не должна мельчить остальные, ей текст обрежется
            # многоточием. Ниже пола не опускаемся в любом случае: лучше обрезать
            # хвост одному блоку, чем сделать нечитаемым весь класс.
            floor = max(min_font, int(target * 0.85))
            sizes = sorted(p["size"] for p in band)
            common = max(floor, sizes[len(sizes) // 6])
            for p in band:
                p["size"] = min(p["size"], common)
            log(f"кегль класса ~{target}: общий {common}, блоков {len(band)}")

    # --- проход 3: раскладка строк окончательным кеглем
    for p in plans:
        avail_w, avail_h = p["avail"]
        font = _font(p["size"], p["text"])
        lines = _wrap(draw, p["text"], font, avail_w)
        line_h = max(1, int(p["size"] * spacing))
        room = max(1, int(avail_h // line_h))
        if len(lines) > room:
            # не влезло даже на минимуме: обрезаем явно, а не наползаем на соседа.
            # Полный текст всё равно есть в буфере обмена и в текстовом режиме.
            lines = lines[:room]
            lines[-1] = lines[-1].rstrip(" ,;:-") + "…"
        p["font"], p["lines"], p["line_h"] = font, lines, line_h

    # --- проход 4а: закрашиваем весь исходный текст
    for p in plans:
        x0, y0, x1, y1 = p["box"]
        w, h = x1 - x0, y1 - y0
        max_right, max_bottom = p["limit"]
        used_w = int(max(draw.textlength(ln, font=p["font"]) for ln in p["lines"])) + 6
        used_h = len(p["lines"]) * p["line_h"] + 2
        px1 = min(max_right, x0 + max(w, used_w))
        py1 = min(max_bottom, y0 + max(h, used_h))

        # фон берём из ИСХОДНОЙ картинки: на out уже могли лечь чужие заливки
        region = src.crop((x0, y0, px1, py1))
        bg, even = _background_of(region)             # цвет фона и ровный ли он
        flat = Image.new("RGB", region.size, bg)
        if even:
            # Ровный фон — страница, карточка, ячейка таблицы — закрашиваем
            # начисто. Смесь с размытым оригиналом оставляла на белом поле
            # серую тень ростом с исходную надпись: перевод короче и мельче, и
            # «стёртое» место торчало прямоугольником рядом с каждой строкой.
            out.paste(flat, (x0, y0))
        else:
            # Под текстом картинка или градиент: там ровная плашка выглядит
            # заплаткой, и размытие честнее.
            base = region.filter(ImageFilter.GaussianBlur(max(2, h // 6)))
            out.paste(Image.blend(base, flat, 0.93), (x0, y0))

        light = (0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]) > 140
        p["color"] = (20, 20, 20) if light else (243, 243, 243)
        p["rect"] = (px1, py1)

    # --- проход 4б: и только теперь пишем перевод
    for p in plans:
        x0, y0 = p["box"][0], p["box"][1]
        py1 = p["rect"][1]
        y = y0 + max(0, ((py1 - y0) - len(p["lines"]) * p["line_h"]) // 2)
        rtl = _is_rtl_text(p["text"])
        for line in p["lines"]:
            shown = bidi_display(line) if rtl else line
            # текст справа налево и прижимается к правому краю блока
            x = x0 + 2
            if rtl:
                x = max(x0 + 2, p["rect"][0] - 2
                        - int(draw.textlength(shown, font=p["font"])))
            draw.text((x, y), shown, font=p["font"], fill=p["color"])
            y += p["line_h"]

    # срезаем неиспользованный запас снизу
    used_bottom = max(p["rect"][1] for p in plans)
    return out.crop((0, 0, out.width, min(src.height, max(base_h, used_bottom + 4))))


# --------------------------------------------------------------------------------------
#  Окно выделения области
# --------------------------------------------------------------------------------------
class SelectionOverlay:
    """Замороженный снимок экрана на весь рабочий стол, на нём мышью выделяется область."""

    def __init__(self, root, screenshot, origin, on_done):
        self.root = root
        self.screenshot = screenshot
        self.ox, self.oy = origin
        self.on_done = on_done
        self.start = None
        self.rect_id = None
        self.hint_id = None
        self.hint_bg = None

        vx, vy, vw, vh = virtual_screen_rect()
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self.win.attributes("-topmost", True)
        self.win.configure(cursor="crosshair", bg="black")

        dark = ImageEnhance.Brightness(screenshot).enhance(0.55)
        from PIL import ImageTk  # импорт здесь: нужен уже созданный Tk
        self.photo = ImageTk.PhotoImage(dark)
        self.canvas = tk.Canvas(self.win, width=vw, height=vh, highlightthickness=0,
                                bd=0, bg="black", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_text(vw // 2 + vx - vx, 28, anchor="n", fill="#ffffff",
                                font=("Segoe UI", 13),
                                text="Выделите область с текстом  •  Esc — отмена")

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Button-3>", lambda e: self.cancel())

        self.win.focus_force()
        self.canvas.focus_set()

    def _press(self, event):
        self.start = (event.x, event.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                    outline="#4da3ff", width=2)

    def _drag(self, event):
        if not self.start:
            return
        x0, y0 = self.start
        self.canvas.coords(self.rect_id, x0, y0, event.x, event.y)
        label = f"{abs(event.x - x0)} × {abs(event.y - y0)}"
        ty = min(y0, event.y) - 22
        if ty < 4:
            ty = max(y0, event.y) + 6
        tx = min(x0, event.x)
        if self.hint_id:
            self.canvas.delete(self.hint_id)
            self.canvas.delete(self.hint_bg)
        self.hint_bg = self.canvas.create_rectangle(tx, ty, tx + 9 * len(label) + 10, ty + 20,
                                                    fill="#1b1b1b", outline="")
        self.hint_id = self.canvas.create_text(tx + 6, ty + 3, anchor="nw", text=label,
                                               fill="#ffffff", font=("Segoe UI", 10))

    def _release(self, event):
        if not self.start:
            return self.cancel()
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.close()
        if box[2] - box[0] < 6 or box[3] - box[1] < 6:
            return
        self.on_done(box)

    def cancel(self):
        self.close()

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------------------
#  Окно результата
# --------------------------------------------------------------------------------------
SB = 10          # толщина полосы прокрутки; на столько же ужимаем содержимое
_scroll_style = None      # для какой палитры стиль уже покрашен


def _scrollbar(parent, orient, command):
    """Плоская полоса прокрутки без стрелок, под цвет окна.

    У классической tk.Scrollbar стрелки не убираются и цвета на Windows
    получаются системные — в тёмном окне она выглядит чужой. У ttk можно
    выкинуть стрелки из раскладки стиля и покрасить всё явно.

    Стиль в ttk один на всё приложение, поэтому при смене оформления его надо
    перекрашивать — запоминаем, для какой палитры он собран.
    """
    global _scroll_style
    style = ttk.Style()
    if _scroll_style != theme_name():
        c = theme()
        try:
            style.theme_use("clam")        # только у clam красится целиком
        except Exception:
            pass
        for side, axis in (("Vertical", "ns"), ("Horizontal", "ew")):
            name = f"Dark.{side}.TScrollbar"
            try:
                style.layout(name, [(f"{side}.Scrollbar.trough", {
                    "children": [(f"{side}.Scrollbar.thumb",
                                  {"expand": "1", "sticky": "nswe"})],
                    "sticky": axis})])
            except Exception:
                pass
            style.configure(name, background=c["sb_thumb"], troughcolor=c["bg"],
                            bordercolor=c["bg"], darkcolor=c["sb_thumb"],
                            lightcolor=c["sb_thumb"], arrowsize=SB, relief="flat")
            style.map(name, background=[("active", c["sb_active"])])
        _scroll_style = theme_name()
    side = "Vertical" if orient == "vertical" else "Horizontal"
    return ttk.Scrollbar(parent, orient=orient, command=command,
                         style=f"Dark.{side}.TScrollbar")


class Tooltip:
    """Всплывающая подсказка у виджета: появляется с паузой, гаснет при уходе."""

    DELAY = 450

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Button-1>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self.job = self.widget.after(self.DELAY, self._show)

    def _cancel(self):
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _show(self):
        self.job = None
        if self.tip is not None or not self.text:
            return
        try:
            tip = tk.Toplevel(self.widget)
            tip.overrideredirect(True)
            # окно результата само поверх всех, иначе подсказка спрячется под ним
            tip.attributes("-topmost", True)
            c = theme()
            tip.configure(bg=c["line"])      # рамка в один пиксель
            tk.Label(tip, text=self.text, bg=c["tip_bg"], fg=c["fg"],
                     font=("Segoe UI", 9), padx=7, pady=3).pack(padx=1, pady=1)
            tip.update_idletasks()
            tw, th = tip.winfo_width(), tip.winfo_height()
            cx = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            # у края экрана подсказка уезжала бы за монитор: держим её внутри
            mx, my, mw, mh = monitor_rect_at(cx, y)
            x = min(max(cx - tw // 2, mx + 2), max(mx + 2, mx + mw - tw - 2))
            if y + th > my + mh:
                y = self.widget.winfo_rooty() - th - 6
            tip.geometry("+%d+%d" % (int(x), int(y)))
            self.tip = tip
        except Exception:
            self.tip = None

    def hide(self, event=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


# Клавиши, которые текст не меняют: по ним в поле «только для чтения» ходить
# можно. Всё остальное там перехватывается.
NAV_KEYS = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}

# Коды клавиш Windows: в отличие от названий, они одинаковы при любой раскладке.
# При русской раскладке Ctrl+C приходит как «Cyrillic_es», штатная привязка Tk
# <Control-c> не срабатывает, и выделенное не копировалось вовсе.
KEY_A, KEY_C, KEY_INSERT = 65, 67, 45


class ResultWindow:
    """Окно поверх всех: либо картинка с наложенным переводом, либо текстовая панель."""

    _open = []

    MIN_SCALE, MAX_SCALE = 0.5, 3.0
    MIN_VIEW_W, MIN_VIEW_H = 200, 80

    def __init__(self, root, screen_xy, image=None, translated="", original="",
                 rerender=None, retranslate=None, on_target=None, on_theme=None,
                 post=None):
        self.root = root
        self.translated = translated
        self.original = original
        self.image = image                 # чётко отрисованная картинка
        self.show_text = image is None
        self.text_kind = "trans"           # какой текст показываем: "trans" или "orig"
        self._drag_from = None
        self._text_dragging = False   # тянут окно за пустое место в тексте
        # пересборка картинки с другим увеличением; None — если перевод текстовый
        self.rerender = rerender
        # перевод того же снимка на другой язык; None — если пересчитывать нечего
        # (сообщение об ошибке, перевод буфера обмена)
        self.retranslate = retranslate
        self.on_target = on_target
        self.on_theme = on_theme
        # Готовое из фонового потока возвращаем в поток окна: Tk не терпит
        # обращений со стороны, и after() оттуда молча не срабатывает.
        self.post = post or (lambda fn, *a: fn(*a))
        self._working = False
        # Две независимые ручки:
        #   scale   — крупность содержимого (кегль текста / разрешение оверлея), ползунок;
        #   win_w/h — размер окна, уголок. Не влияют друг на друга: если содержимое
        #             больше окна, оно прокручивается.
        # _render_zoom — в каком разрешении картинка отрисована на самом деле.
        # Пока тянут ползунок, он расходится со scale: показываем быструю растяжку,
        # а честную перерисовку делаем один раз, когда отпустят.
        self.scale = _clamp(CFG.get("overlay_zoom", 1.0), self.MIN_SCALE, self.MAX_SCALE, 1.0)
        self._render_zoom = self.scale
        self.win_w = self.win_h = None      # None — подобрать по содержимому при первой сборке
        self._resize_from = None
        self._commit_job = None
        self._slider_sync = False
        self._sb_v = self._sb_h = False      # какие полосы прокрутки сейчас есть
        self.canvas = None
        self.slider = None
        self.scale_label = None
        self.text_frame = None
        self.text_widget = None
        self.text_scroll = None
        self._text_sb_shown = False

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=theme()["bg"])
        self.x, self.y = screen_xy

        self.body = tk.Frame(self.win, bg=theme()["bg"])
        self.body.pack(fill="both", expand=True)
        # Поля вокруг содержимого — тоже место, за которое тянут окно.
        # Заголовка у этого окна нет, и хвататься больше не за что.
        self.body.bind("<ButtonPress-1>", self._press)
        self.body.bind("<B1-Motion>", self._move)
        self.body.bind("<ButtonRelease-1>", self._drop)

        self._build()
        ResultWindow._open.append(self)
        self.win.bind("<Escape>", lambda e: self.close())

    def _build(self, focus=True):
        c = theme()
        self.win.configure(bg=c["bg"])
        self.body.configure(bg=c["bg"])
        for child in self.body.winfo_children():
            child.destroy()

        self.canvas = self.slider = self.scale_label = None
        self.text_frame = self.text_widget = self.text_scroll = None

        vw, vh = self._viewport()
        if self.image is not None and not self.show_text:
            from PIL import ImageTk
            shown = self._display_image()
            holder = tk.Frame(self.body, bg=c["bg"], width=vw, height=vh)
            holder.pack_propagate(False)
            holder.grid_propagate(False)      # дети на grid — pack_propagate им не указ
            holder.pack(padx=10, pady=(10, 4))

            # место под полосы прокрутки вычитаем из холста, а не прибавляем к
            # окну: иначе появление полосы дёргало бы размер окна
            need_v = shown.height > vh
            need_h = shown.width > vw
            if need_v and shown.width > vw - SB:
                need_h = True
            if need_h and shown.height > vh - SB:
                need_v = True
            cw = max(20, vw - (SB if need_v else 0))
            ch = max(20, vh - (SB if need_h else 0))
            self._sb_v, self._sb_h = need_v, need_h

            canvas = tk.Canvas(holder, width=cw, height=ch, highlightthickness=0, bd=0,
                               bg=c["bg"], scrollregion=(0, 0, shown.width, shown.height))
            canvas.grid(row=0, column=0)
            if need_v:
                vs = _scrollbar(holder, "vertical", canvas.yview)
                vs.grid(row=0, column=1, sticky="ns")
                canvas.configure(yscrollcommand=vs.set)
            if need_h:
                hs = _scrollbar(holder, "horizontal", canvas.xview)
                hs.grid(row=1, column=0, sticky="ew")
                canvas.configure(xscrollcommand=hs.set)

            self.photo = ImageTk.PhotoImage(shown)
            # картинка мельче окна — ставим по центру, а не в угол
            canvas.create_image(max(0, (cw - shown.width) // 2),
                                max(0, (ch - shown.height) // 2),
                                image=self.photo, anchor="nw")
            canvas.bind("<ButtonPress-1>", self._press)
            canvas.bind("<B1-Motion>", self._move)
            canvas.bind("<ButtonRelease-1>", self._drop)
            canvas.bind("<MouseWheel>", self._wheel_scroll)
            canvas.bind("<Control-MouseWheel>", self._wheel)
            self.canvas = canvas
        else:
            frame = tk.Frame(self.body, bg=c["bg"], width=vw, height=vh)
            frame.pack_propagate(False)
            frame.grid_propagate(False)
            frame.pack(padx=10, pady=(10, 4))
            self.text_frame = frame
            self._sb_v, self._sb_h = True, False    # вертикальная полоса тут всегда

            fs = self._font_size()
            text = tk.Text(frame, wrap="word", bg=c["bg"], fg=c["fg"], bd=0,
                           insertbackground=c["fg"],
                           font=("Segoe UI", fs), padx=4, pady=2)
            # grid, а не pack: при pack текст с expand=True съедает всю ячейку,
            # и добавленной позже полосе достаётся ширина в один пиксель
            frame.grid_rowconfigure(0, weight=1)
            frame.grid_columnconfigure(0, weight=1)
            text.grid(row=0, column=0, sticky="nsew")
            scroll = _scrollbar(frame, "vertical", text.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            scroll.grid_remove()                # покажем, когда будет что листать
            self.text_scroll = scroll
            self._text_sb_shown = False
            text.configure(yscrollcommand=self._on_text_scroll)

            body = self.original if self.text_kind == "orig" else self.translated
            text.insert("end", body or tr("no_text"))
            self._make_readonly(text)
            self.text_widget = text
            # колесо здесь листает текст, поэтому кегль — на Ctrl+колесо
            text.bind("<Control-MouseWheel>", self._wheel)
            # За текст окно не потащишь: там левой кнопкой выделяют. Зато
            # пустое место под текстом и справа от строк ничем не занято.
            text.bind("<ButtonPress-1>", self._text_press)
            text.bind("<B1-Motion>", self._text_motion)
            text.bind("<ButtonRelease-1>", self._text_drop)
        width = vw + 20

        bar = tk.Frame(self.body, bg=c["bar"], height=32)
        bar.pack(fill="x")
        bar.bind("<ButtonPress-1>", self._press)
        bar.bind("<B1-Motion>", self._move)
        bar.bind("<ButtonRelease-1>", self._drop)

        def button(txt, cmd, fg=None, bg=None, hover=None,
                   padx=10, font=("Segoe UI", 10), tip=None, side="left"):
            fg, bg = fg or c["btn"], bg or c["bar"]
            hover = hover or c["hover"]
            b = tk.Label(bar, text=txt, bg=bg, fg=fg, font=font,
                         padx=padx, pady=6, cursor="hand2")
            b.pack(side=side)
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.configure(bg=hover))
            b.bind("<Leave>", lambda e: b.configure(bg=bg))
            if tip:
                Tooltip(b, tip)
            return b

        def mode_button(txt, active, cmd):
            # текущий режим подсвечен фоном, поэтому наведение на него ярче обычного
            return button(txt, cmd, fg=c["active_fg"] if active else c["btn"],
                          bg=c["active"] if active else c["bar"],
                          hover=c["hover_active"] if active else c["hover"], padx=8)

        # набор кнопок от режима не зависит, поэтому при переключении панель
        # не меняет ширину и окно не скачет
        showing_orig = self.show_text and self.text_kind == "orig"
        button("⧉", self.copy, padx=9, font=("Segoe UI", 13),
               tip=(tr("copy_orig") if showing_orig else
                    tr("copy_trans") if self.original else tr("copy")))
        if self.image is not None:
            mode_button(tr("mode_image"), not self.show_text, self.show_image)
        if self.original:
            mode_button(tr("mode_orig"), showing_orig, lambda: self.show_kind("orig"))
        if self.image is not None or self.original:
            mode_button(tr("mode_trans"), self.show_text and not showing_orig,
                        lambda: self.show_kind("trans"))

        # Выбор языка перевода прямо здесь: чаще всего он и нужен сразу после
        # того, как перевод увидел глазами. Пересчитываем тот же снимок, второй
        # раз распознавать нечего.
        self.target_btn = None
        if self.retranslate is not None:
            if self._working:
                tk.Label(bar, text=f'{globe_icon(bar)}  {tr("translating")}',
                         bg=c["bar"], fg=c["dim"],
                         font=("Segoe UI", 10), padx=10, pady=6).pack(side="left")
            else:
                self.target_btn = button(
                    f'{globe_icon(bar)}  {target_title(str(CFG.get("target_lang", "ru")))}  ⌄',
                    self._pick_target, padx=8, tip=tr("target_tip"))

        # ползунок крупности: в тексте — кегль шрифта, в картинке — масштаб оверлея.
        # Размера окна не касается, за него отвечает уголок.
        if self.show_text or self.rerender is not None:
            tk.Label(bar, text="│", bg=c["bar"], fg=c["line"],
                     font=("Segoe UI", 10), pady=6).pack(side="left")
            tk.Label(bar, text="A", bg=c["bar"], fg=c["dim"],
                     font=("Segoe UI", 8), padx=(2), pady=6).pack(side="left")
            self.slider = tk.Scale(bar, from_=int(self.MIN_SCALE * 100),
                                   to=int(self.MAX_SCALE * 100), orient="horizontal",
                                   length=104, width=8, sliderlength=14, showvalue=0,
                                   # bg в tk.Scale красит и сам бегунок, поэтому
                                   # светлый: иначе он сливается с дорожкой
                                   resolution=5, bg=c["slider"], fg=c["btn"],
                                   troughcolor=c["trough"], activebackground=c["accent"],
                                   highlightthickness=0, bd=0, sliderrelief="flat",
                                   cursor="hand2", command=self._slider_move)
            self._slider_sync = True                # set() дёргает command — глушим
            self.slider.set(int(round(self.scale * 100)))
            self._slider_sync = False
            self.slider.pack(side="left", padx=(0, 2))
            self.slider.bind("<ButtonRelease-1>", lambda e: self.set_zoom(self.scale))
            tk.Label(bar, text="A", bg=c["bar"], fg=c["dim"],
                     font=("Segoe UI", 13), padx=2, pady=3).pack(side="left")
            self.scale_label = tk.Label(bar, text=f"{int(round(self.scale * 100))}%",
                                        bg=c["bar"], fg=c["faint"], width=5,
                                        font=("Segoe UI", 9), padx=4, pady=6)
            self.scale_label.pack(side="left")
        close = tk.Label(bar, text="✕", bg=c["bar"], fg=c["btn"], font=("Segoe UI", 11),
                         padx=12, pady=5, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        close.bind("<Enter>", lambda e: close.configure(bg=c["close"], fg="#ffffff"))
        close.bind("<Leave>", lambda e: close.configure(bg=c["bar"], fg=c["btn"]))

        # Переключатель оформления: показываем не то, что сейчас, а то, что
        # получится по нажатию, — солнце в тёмном окне и месяц в светлом. Так
        # понятнее, чем значок текущего состояния: видно, куда ведёт кнопка.
        if self.on_theme is not None:
            dark_now = theme_name() == "dark"
            button("☀" if dark_now else "☾",
                   lambda: self.on_theme("light" if dark_now else "dark"),
                   padx=9, font=("Segoe UI", 12), side="right",
                   tip=tr("theme_light") if dark_now else tr("theme_dark"))

        # уголок в правом нижнем углу — тянуть мышью, как за край обычного окна
        if self.show_text or self.rerender is not None:
            grip = tk.Label(bar, text="◢", bg=c["bar"], fg=c["grip"],
                            font=("Segoe UI", 10), padx=8, pady=5, cursor="sizing")
            grip.pack(side="right")
            grip.bind("<ButtonPress-1>", self._grip_press)
            grip.bind("<B1-Motion>", self._grip_drag)
            grip.bind("<ButtonRelease-1>", self._grip_release)

        promo = promo_line()
        if promo:
            # Ð¡ÑÑÐ¾ÐºÐ° ÑÐ¾Ð±Ð¸ÑÐ°ÐµÑÑÑ Ð¸Ð· ÐºÑÑÐºÐ¾Ð²: Ñ ÐºÐ°Ð¶Ð´Ð¾Ð¹ ÑÑÑÐ»ÐºÐ¸ ÑÐ²Ð¾Ð¹ Ð°Ð´ÑÐµÑ. ÐÐ´Ð½Ð¾Ð¹
            # Ð½Ð°Ð´Ð¿Ð¸ÑÑÑ ÑÑÑ Ð½Ðµ Ð¾Ð±Ð¾Ð¹ÑÐ¸ÑÑ: Tkinter Ð½Ðµ ÑÐ¼ÐµÐµÑ Ð²ÐµÑÐ°ÑÑ ÑÐ°Ð·Ð½ÑÐµ Ð´ÐµÐ¹ÑÑÐ²Ð¸Ñ
            # Ð½Ð° ÑÐ°ÑÑÐ¸ Ð¾Ð´Ð½Ð¾Ð³Ð¾ Label.
            parts = promo_parts(promo[0], promo[1])
            strip = tk.Frame(self.body, bg=c["bar"])
            strip.pack(fill="x")
            for i, (chunk, link) in enumerate(parts):
                lbl = tk.Label(strip, text=chunk, bg=c["bar"],
                               fg=c["btn"] if link else c["dim"],
                               font=("Segoe UI", 9), pady=4,
                               cursor="hand2" if link else "arrow")
                lbl.pack(side="left", padx=(12, 0) if i == 0 else 0)
                if link:
                    lbl.bind("<Button-1>", lambda e, u=link: webbrowser.open(u))
                    lbl.bind("<Enter>",
                             lambda e, w=lbl: w.configure(font=("Segoe UI", 9, "underline")))
                    lbl.bind("<Leave>", lambda e, w=lbl: w.configure(font=("Segoe UI", 9)))

        self.win.update_idletasks()
        self._place(width)

    def _place(self, width, focus=True):
        # держимся того монитора, на котором окно сейчас стоит, а не главного
        mx, my, mw, mh = monitor_rect_at(self.x, self.y)
        w = max(self.win.winfo_reqwidth(), width)
        h = self.win.winfo_reqheight()
        x = min(max(self.x, mx), max(mx, mx + mw - w))
        y = min(max(self.y, my), max(my, my + mh - h))
        self.x, self.y = int(x), int(y)
        self.win.geometry(f"{w}x{h}+{self.x}+{self.y}")
        if focus:                       # при прокрутке колесом фокус не дёргаем
            self.win.focus_force()

    def _viewport(self):
        """Размер области содержимого: задан уголком, при первой сборке — по содержимому."""
        if self.win_w is None or self.win_h is None:
            if self.image is not None and not self.show_text:
                shown = self._display_image()
                self.win_w, self.win_h = shown.width, shown.height
            else:
                self.win_w, self.win_h = 540, 300
        _, _, mon_w, mon_h = monitor_rect_at(self.x, self.y)
        return (max(self.MIN_VIEW_W, min(int(mon_w * 0.9), int(self.win_w))),
                max(self.MIN_VIEW_H, min(int(mon_h * 0.85), int(self.win_h))))

    def _on_text_scroll(self, first, last):
        """Полосу в тексте показываем, только когда есть что листать, — как у картинки.

        Решение принимаем по значениям, которые Tk сам присылает через
        yscrollcommand: любые собственные замеры до раскладки окна врут.
        """
        if self.text_scroll is None:
            return
        self.text_scroll.set(first, last)
        need = float(first) > 0.0 or float(last) < 1.0
        if need == self._text_sb_shown:
            return
        self._text_sb_shown = need
        self._sb_v = need
        # Менять раскладку прямо в обработчике прокрутки нельзя: такой pack()
        # Tk молча теряет — полоса остаётся шириной в пиксель и не показывается.
        self.win.after_idle(self._apply_text_scroll)

    def _apply_text_scroll(self):
        if self.text_scroll is None:
            return
        try:
            if self._text_sb_shown:
                self.text_scroll.grid()
            else:
                self.text_scroll.grid_remove()
        except Exception:
            pass

    def _font_size(self):
        return max(7, int(round(_clamp(CFG.get("font_size", 14), 6, 40, 14) * self.scale)))

    def _pick_target(self):
        """Список языков под кнопкой. Каждый назван на себе самом — так его
        узнают при любом языке интерфейса."""
        codes = [code for code, _ in TARGET_LANGS]
        current = str(CFG.get("target_lang", "ru"))
        if current not in codes:
            codes.insert(0, current)

        c = theme()
        top = tk.Toplevel(self.win)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=c["line"])                  # рамка в один пиксель
        frame = tk.Frame(top, bg=c["bg"])
        frame.pack(padx=1, pady=1)

        per_col = 12                                 # длинный список кладём в колонки
        for i, code in enumerate(codes):
            active = code == current
            cell = tk.Label(frame, text=target_title(code), anchor="w",
                            bg=c["active"] if active else c["bg"],
                            fg=c["active_fg"] if active else c["btn"],
                            font=("Segoe UI", 10), padx=14, pady=4, cursor="hand2")
            cell.grid(row=i % per_col, column=i // per_col, sticky="ew")
            cell.bind("<Enter>", lambda e, w=cell: w.configure(bg=c["hover_active"]))
            cell.bind("<Leave>", lambda e, w=cell, a=active:
                      w.configure(bg=c["active"] if a else c["bg"]))
            cell.bind("<Button-1>", lambda e, code=code: (top.destroy(),
                                                          self.set_target(code)))

        top.update_idletasks()
        anchor = self.target_btn
        x = anchor.winfo_rootx() if anchor else self.win.winfo_rootx()
        y = (anchor.winfo_rooty() if anchor else self.win.winfo_rooty()) - top.winfo_height() - 4
        mx, my, mw, mh = monitor_rect_at(x, y)
        x = min(max(x, mx + 2), max(mx + 2, mx + mw - top.winfo_width() - 2))
        if y < my + 2:                               # не влезло сверху — покажем снизу
            y = (anchor.winfo_rooty() + anchor.winfo_height() + 4) if anchor else my + 2
        top.geometry("+%d+%d" % (int(x), int(y)))
        top.bind("<Escape>", lambda e: top.destroy())
        top.bind("<FocusOut>", lambda e: top.destroy())
        top.focus_force()

    def set_target(self, code):
        """Переводим этот же снимок на другой язык — не переснимая экран."""
        if self.retranslate is None or self._working:
            return
        if code == str(CFG.get("target_lang", "ru")):
            return
        if self.on_target:
            self.on_target(code)          # сохранить выбор и подтянуть надписи
        else:
            CFG["target_lang"] = code
        self._working = True
        self._build(focus=False)

        def work():
            result = None
            try:
                result = self.retranslate(code)
            except Exception:
                traceback.print_exc()

            def apply():
                self._working = False
                if result is not None:
                    full, render = result
                    self.translated = full
                    if render is not None:
                        self.rerender = render
                        try:
                            zoom = max(1.0, self.scale)
                            self.image = render(zoom)
                            self._render_zoom = zoom
                        except Exception:
                            traceback.print_exc()
                self._build(focus=False)

            self.post(apply)

        threading.Thread(target=work, daemon=True).start()

    def show_image(self):
        """Картинка с наложенным переводом. Размер окна общий для всех режимов."""
        if not self.show_text:
            return
        self.show_text = False
        self._build()

    def show_kind(self, kind):
        """Текстовый режим: "orig" — оригинал, "trans" — перевод."""
        if self.show_text and self.text_kind == kind:
            return
        self.show_text = True
        self.text_kind = kind
        self._build()

    def _display_image(self):
        """Картинка под текущий масштаб: либо чёткий оригинал, либо его растяжка."""
        if self.image is None:
            return None
        if abs(self.scale - self._render_zoom) < 0.01:
            return self.image
        k = self.scale / self._render_zoom
        return self.image.resize((max(1, int(self.image.width * k)),
                                  max(1, int(self.image.height * k))), Image.LANCZOS)

    def _preview(self, z):
        """Крупность меняем на существующих виджетах, размер окна не трогаем.

        Пересобирать на каждое движение мыши нельзя: пересборка сносит виджет,
        на котором зажата кнопка, события уезжают в бар — и окно скачет.
        """
        # round — иначе шаги по 0.1 накапливают хвост вида 1.5000000000000004
        self.scale = round(max(self.MIN_SCALE, min(self.MAX_SCALE, z)), 3)
        if self.scale_label is not None:
            self.scale_label.configure(text=f"{int(round(self.scale * 100))}%")
        if self.slider is not None and not self._slider_sync:
            self._slider_sync = True
            self.slider.set(int(round(self.scale * 100)))
            self._slider_sync = False

        if self.show_text:
            if self.text_widget is None:
                return
            fs = self._font_size()
            self.text_widget.configure(font=("Segoe UI", fs))
            return

        shown = self._display_image()
        if shown is None or self.canvas is None:
            return
        from PIL import ImageTk
        cw = int(self.canvas.cget("width"))
        ch = int(self.canvas.cget("height"))
        self.photo = ImageTk.PhotoImage(shown)
        self.canvas.delete("all")
        self.canvas.create_image(max(0, (cw - shown.width) // 2),
                                 max(0, (ch - shown.height) // 2),
                                 image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, shown.width, shown.height))

    def _slider_move(self, value):
        if self._slider_sync:
            return
        self._preview(float(value) / 100.0)

    def set_zoom(self, z):
        """Окончательный масштаб: в картинке — новое разрешение, в тексте — кегль."""
        if self._commit_job is not None:
            try:
                self.win.after_cancel(self._commit_job)
            except Exception:
                pass
            self._commit_job = None
        self.scale = round(max(self.MIN_SCALE, min(self.MAX_SCALE, z)), 3)
        if not self.show_text and self.rerender is not None:
            # мельче 1× рисовать нет смысла — уменьшаем уже готовую картинку
            target = max(1.0, self.scale)
            if abs(target - self._render_zoom) > 0.01:
                try:
                    self.image = self.rerender(target)
                    self._render_zoom = target
                except Exception:
                    traceback.print_exc()
        self._build(focus=False)

    def _wheel(self, event):
        self._preview(self.scale + (0.1 if event.delta > 0 else -0.1))
        if self._commit_job is not None:
            try:
                self.win.after_cancel(self._commit_job)
            except Exception:
                pass
        # крутить можно быстро — чёткую перерисовку делаем, когда остановились
        self._commit_job = self.win.after(300, lambda: self.set_zoom(self.scale))
        return "break"        # колесо без Ctrl листает содержимое

    def _wheel_scroll(self, event):
        """Колесо над картинкой листает её, если она не помещается в окно."""
        if self.canvas is not None:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _grip_press(self, event):
        vw, vh = self._viewport()
        self._resize_from = (event.x_root, event.y_root, vw, vh)

    def _grip_drag(self, event):
        """Уголок меняет только размер окна. Крупность содержимого не трогаем."""
        if not self._resize_from:
            return
        x0, y0, w0, h0 = self._resize_from
        self.win_w = max(self.MIN_VIEW_W, w0 + event.x_root - x0)
        self.win_h = max(self.MIN_VIEW_H, h0 + event.y_root - y0)
        vw, vh = self._viewport()
        holder = self.text_frame if self.show_text else (
            self.canvas.master if self.canvas is not None else None)
        if holder is None:
            return
        holder.configure(width=vw, height=vh)
        if not self.show_text and self.canvas is not None:
            # полосы прокрутки пересчитаются на отпускании, пока просто тянем холст
            self.canvas.configure(width=max(20, vw - (SB if self._sb_v else 0)),
                                  height=max(20, vh - (SB if self._sb_h else 0)))
        self.win.update_idletasks()
        self._place(vw + 20, focus=False)

    def _grip_release(self, event):
        if not self._resize_from:
            return
        self._resize_from = None
        self._build(focus=False)       # полосы прокрутки могли появиться или пропасть

    def _make_readonly(self, text):
        """Поле только для чтения, но с живым выделением и копированием.

        Через state="disabled" этого не добиться: такое поле в Tk не берёт
        фокус, нажатый Ctrl+C уходить некуда, и кусок текста нельзя было
        скопировать — только целиком, кнопкой. Поэтому поле остаётся обычным, а
        от правки его бережёт разбор клавиш.
        """
        def copy_selection(_=None):
            try:
                chunk = text.get("sel.first", "sel.last")
            except tk.TclError:
                return "break"                 # ничего не выделено
            if chunk:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(chunk)
                    self.root.update()
                except Exception:
                    pass
            return "break"

        def select_all(_=None):
            text.tag_add("sel", "1.0", "end-1c")
            text.focus_set()
            return "break"

        def on_key(event):
            if event.state & 0x4:                       # Ctrl
                if event.keycode in (KEY_C, KEY_INSERT):
                    return copy_selection()
                if event.keycode == KEY_A:
                    return select_all()
                if event.keysym in NAV_KEYS:
                    return None                # Ctrl+стрелка — по словам
                return "break"                 # вставку и вырезание не пускаем
            if event.keysym in NAV_KEYS:
                return None
            return "break"

        text.bind("<Key>", on_key)

        # Меню по правой кнопке: про Ctrl+C догадается не каждый, а кнопка ⧉
        # рядом копирует весь текст целиком, не выделенный кусок.
        menu = tk.Menu(text, tearoff=0)
        menu.add_command(label=tr("copy_sel"), command=copy_selection)
        menu.add_command(label=tr("select_all"), command=select_all)

        def popup(event):
            text.focus_set()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        text.bind("<Button-3>", popup)

    def copy(self):
        # копируем то, что сейчас на экране: в режиме оригинала — оригинал
        text = (self.original if self.show_text and self.text_kind == "orig"
                and self.original else self.translated)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        except Exception:
            pass

    def _press(self, event):
        self._drag_from = (event.x_root, event.y_root,
                           self.win.winfo_x(), self.win.winfo_y())

    def _move(self, event):
        # пока тянут уголок, окно не таскаем: иначе два обработчика дерутся
        # за одно и то же движение мыши
        if not self._drag_from or self._resize_from:
            return
        sx, sy, wx, wy = self._drag_from
        # запоминаем новое место: иначе следующая перерисовка (масштаб, смена режима)
        # вернёт окно туда, где его открыли, — заметнее всего на нескольких мониторах
        self.x = wx + event.x_root - sx
        self.y = wy + event.y_root - sy
        self.win.geometry(f"+{int(self.x)}+{int(self.y)}")

    def _text_press(self, event):
        """Нажатие в тексте: пустое место двигает окно, сам текст — выделяется.

        У окна нет заголовка, и в режиме картинки его таскают прямо за картинку.
        В тексте так нельзя: левая кнопка там выделяет. Но ниже последней строки
        и правее её конца текста нет — за это пустое поле и тянут, ожидая, что
        окно поедет.
        """
        w = event.widget
        pos = w.index(f"@{event.x},{event.y}")
        self._text_dragging = (w.compare(pos, ">=", "end-1c")
                               or w.compare(pos, "==", f"{pos} lineend"))
        if self._text_dragging:
            w.focus_set()          # иначе Ctrl+C и Ctrl+A перестанут доходить
            self._press(event)
            return "break"         # выделению начаться не даём
        return None

    def _text_motion(self, event):
        if self._text_dragging:
            self._move(event)
            return "break"
        return None

    def _text_drop(self, event):
        if self._text_dragging:
            self._text_dragging = False
            self._drop(event)
            return "break"
        return None

    def _drop(self, event):
        """Кнопку отпустили — якорь перетаскивания больше не действителен."""
        self._drag_from = None

    def close(self):
        if self in ResultWindow._open:
            ResultWindow._open.remove(self)
        if self._commit_job is not None:
            try:
                self.win.after_cancel(self._commit_job)
            except Exception:
                pass
            self._commit_job = None
        try:
            self.win.destroy()
        except Exception:
            pass

    @classmethod
    def close_all(cls):
        for w in list(cls._open):
            w.close()


class Toast:
    """Небольшая плашка со статусом (например «Распознаю…»)."""

    def __init__(self, root, text):
        vx, vy, vw, vh = virtual_screen_rect()
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        c = theme()
        tk.Label(self.win, text=text, bg=c["bg"], fg=c["fg"], padx=18, pady=10,
                 font=("Segoe UI", 11), highlightthickness=1,
                 highlightbackground=c["line"]).pack()
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        self.win.geometry(f"+{vx + (vw - w) // 2}+{vy + vh - h - 90}")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------------------
#  Приложение
# --------------------------------------------------------------------------------------
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tasks = queue.Queue()
        self.busy = False
        self.tray = None
        self._pump()

    # --- мост «фоновый поток -> главный поток Tk»
    def post(self, fn, *args):
        self.tasks.put((fn, args))

    def _pump(self):
        while True:
            try:
                fn, args = self.tasks.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                traceback.print_exc()
        self.root.after(40, self._pump)

    # --- сценарий 1: выделение области
    def start_capture(self):
        if self.busy:
            return
        self.busy = True
        log("захват области: открываю выделение")
        ResultWindow.close_all()
        self.root.update()
        try:
            shot, ox, oy = grab_screen()
        except Exception as e:
            self.busy = False
            print("Не удалось сделать снимок экрана:", e)
            return
        SelectionOverlay(self.root, shot, (ox, oy),
                         lambda box: self._on_area(shot, (ox, oy), box))
        log("окно выделения создано")
        self.root.after(200, lambda: setattr(self, "busy", False))

    def _on_area(self, shot, origin, box):
        crop = shot.crop(box)
        screen_xy = (origin[0] + box[0], origin[1] + box[1])
        toast = Toast(self.root, tr("working"))
        threading.Thread(target=self._work, args=(crop, screen_xy, toast), daemon=True).start()

    def _work(self, crop, screen_xy, toast):
        try:
            refresh_config()          # правку настроек видно без перезапуска
            fetch_promo()             # сам решит, пора ли: не чаще раза в сутки
            t0 = time.perf_counter()
            text, blocks, ocr_langs = ocr_blocks(crop)
            t_ocr = time.perf_counter() - t0
            if not text.strip():
                self.post(self._show_error, toast, screen_xy,
                          tr("not_recognized"))
                return

            texts = [b["text"] for b in blocks]
            hint = TESS_TO_ISO.get(ocr_langs.split("+")[0])

            def translate_all(target):
                """Перевести блоки этого снимка на target -> (текст, отрисовка).

                Отдельной функцией, потому что её зовут дважды: сейчас и когда
                в окне результата выбирают другой язык. Заново распознавать при
                этом нечего — блоки уже есть.
                """
                # блоки, которые уже на нужном языке или переводить в которых нечего
                # (цены, размеры), не трогаем — пусть остаются как есть
                need = [i for i, t in enumerate(texts)
                        if is_translatable(t) and not already_target(t, target)]
                done = list(texts)
                if need:
                    for i, part in zip(need, translate_many([texts[i] for i in need],
                                                            target, hint)):
                        done[i] = part
                log(f"перевод: {len(need)} блоков из {len(texts)} на «{target}»")
                draw = None
                if CFG.get("display_mode", "overlay") == "overlay" and blocks:
                    to_draw = [done[i] if i in set(need) else ""
                               for i in range(len(blocks))]

                    # то же самое, но с другим увеличением — для ползунка крупности
                    def draw(z, crop=crop, blocks=blocks, to_draw=to_draw):
                        return render_overlay(crop, blocks, to_draw, zoom=z)

                return join_blocks(blocks, done), draw

            t0 = time.perf_counter()
            full, rerender = translate_all(CFG.get("target_lang", "ru"))
            t_translate = time.perf_counter() - t0

            t0 = time.perf_counter()
            image = None
            if rerender is not None:
                try:
                    image = rerender(None)
                except Exception:
                    traceback.print_exc()
                    image, rerender = None, None
            log(f"время: распознавание {t_ocr:.2f} с, перевод {t_translate:.2f} с, "
                f"отрисовка {time.perf_counter() - t0:.2f} с")

            self.post(self._show_result, toast, screen_xy, image, full, text, rerender,
                      translate_all)
        except Exception as e:
            traceback.print_exc()
            self.post(self._show_error, toast, screen_xy, f'{tr("error")}: {e}')

    def _show_result(self, toast, screen_xy, image, translated, original, rerender=None,
                     translate_all=None):
        toast.close()
        if CFG.get("auto_copy", True) and translated.strip():
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(translated)
                self.root.update()
            except Exception:
                pass
        ResultWindow(self.root, screen_xy, image=image,
                     translated=translated, original=original, rerender=rerender,
                     retranslate=translate_all, on_target=self._apply_target,
                     on_theme=self._set_theme, post=self.post)

    def _show_error(self, toast, screen_xy, message):
        toast.close()
        ResultWindow(self.root, screen_xy, image=None, translated=message, original="",
                     on_theme=self._set_theme, post=self.post)

    # --- сценарий 2: перевод буфера обмена (текст или скопированная картинка)
    def translate_clipboard(self):
        refresh_config()
        vx, vy, vw, vh = virtual_screen_rect()
        pos = (vx + vw // 2 - 260, vy + vh // 2 - 150)

        try:
            text = self.root.clipboard_get()
        except Exception:
            text = ""

        if text.strip():
            log("буфер обмена: текст")
            toast = Toast(self.root, "Перевожу буфер обмена…")

            def job():
                try:
                    translated, _ = translate(text)
                    self.post(self._show_result, toast, pos, None, translated, text.strip())
                except Exception as e:
                    self.post(self._show_error, toast, pos, f"Ошибка: {e}")

            threading.Thread(target=job, daemon=True).start()
            return

        image = clipboard_image()
        if image is not None:
            log(f"буфер обмена: картинка {image.size}")
            toast = Toast(self.root, "Распознаю картинку из буфера…")
            threading.Thread(target=self._work, args=(image, pos, toast), daemon=True).start()
            return

        log("буфер обмена пуст")
        self._flash(tr("clip_empty"))

    def _flash(self, message, seconds=2.2):
        """Короткое сообщение, которое само исчезает."""
        toast = Toast(self.root, message)
        self.root.after(int(seconds * 1000), toast.close)

    # --- горячие клавиши
    def start_hotkeys(self):
        mapping = {
            CFG.get("hotkey_capture", "<ctrl>+<alt>+z"): lambda: self.post(self.start_capture),
            CFG.get("hotkey_clipboard", "<ctrl>+<alt>+x"): lambda: self.post(self.translate_clipboard),
        }
        if sys.platform == "win32":
            return register_global_hotkeys(mapping)
        try:
            from pynput import keyboard
            listener = keyboard.GlobalHotKeys(mapping)
            listener.daemon = True
            listener.start()
            return listener
        except Exception as e:
            print("Не удалось зарегистрировать горячие клавиши:", e)
            return None

    # --- иконка в трее
    def start_tray(self):
        try:
            import pystray
        except Exception:
            print("pystray не установлен — работаем без иконки в трее")
            return

        icon_img = Image.new("RGB", (64, 64), "#1a73e8")
        d = ImageDraw.Draw(icon_img)
        try:
            f = ImageFont.truetype(FONT_CANDIDATES[0], 34)
        except Exception:
            f = ImageFont.load_default()
        d.text((32, 32), "Я", font=f, fill="white", anchor="mm")

        menu = pystray.Menu(
            # текст пунктов — функциями: меню собирается один раз, а язык
            # интерфейса может смениться в любой момент
            pystray.MenuItem(
                lambda _: f'{tr("menu_capture")} ({self._pretty(CFG["hotkey_capture"])})',
                lambda: self.post(self.start_capture), default=True),
            pystray.MenuItem(
                lambda _: f'{tr("menu_clip")} ({self._pretty(CFG["hotkey_clipboard"])})',
                lambda: self.post(self.translate_clipboard)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: tr("target_set"), self._target_menu(pystray)),
            pystray.MenuItem(lambda _: tr("ocr_set"), self._lang_menu(pystray)),
            pystray.MenuItem(lambda _: tr("service_set"), self._translator_menu(pystray)),
            pystray.MenuItem(lambda _: tr("menu_theme"), self._theme_menu(pystray)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: tr("font_bigger"), lambda: self._bump_font(+0.1)),
            pystray.MenuItem(lambda _: tr("font_smaller"), lambda: self._bump_font(-0.1)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: tr("autostart"), self._toggle_autostart,
                             checked=lambda item: autostart_enabled()),
            pystray.MenuItem(lambda _: tr("settings"), lambda: os.startfile(CONFIG_PATH)),
            pystray.MenuItem(lambda _: tr("quit"), self.quit),
            pystray.Menu.SEPARATOR,
            # Без перевода: «v1.0.1» читается на любом языке интерфейса.
            pystray.MenuItem(f"v{APP_VERSION}", None, enabled=False),
        )
        self.tray = pystray.Icon("screen_translator", icon_img,
                                 f"Экранный переводчик {APP_VERSION}", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _lang_menu(self, pystray):
        """Подменю «Язык на экране»: чем на экране написано, тем и читаем.

        Автоопределение удобно, но стоит времени: отдельный проход на
        письменность (~0.2 с) плюс набор из двух языков вместо одного (ещё
        ~0.35 с на снимок величиной с половину экрана). Когда язык известен
        заранее, честнее назвать его прямо — распознавание выходит вдвое
        быстрее и точнее.
        """
        have = available_ocr_langs() - {"osd"} - HIDDEN_OCR_LANGS
        codes = ["auto"]
        for code in ("eng", "rus"):
            if code in have:
                codes.append(code)
        if {"eng", "rus"} <= have:
            codes.append("eng+rus")
        codes += [c for _, c in sorted((lang_title(c), c) for c in have
                                       if c not in ("eng", "rus"))]

        def item(code):
            return pystray.MenuItem(
                lang_title(code), lambda: self._set_ocr_langs(code), radio=True,
                checked=lambda _, code=code: str(CFG.get("ocr_langs", "auto")) == code)

        return pystray.Menu(*[item(c) for c in codes])

    def _target_menu(self, pystray):
        """Подменю «Переводить на» — язык, на который переводим.

        Текущий язык дописываем в список, даже если его там нет: человек мог
        вписать в config.json что-то своё, и выбор не должен молча пропадать.
        """
        codes = [code for code, _ in TARGET_LANGS]
        current = str(CFG.get("target_lang", "ru"))
        if current not in codes:
            codes.insert(0, current)

        def item(code):
            return pystray.MenuItem(
                target_title(code), lambda: self._set_target_lang(code), radio=True,
                checked=lambda _, code=code: str(CFG.get("target_lang", "ru")) == code)

        return pystray.Menu(*[item(code) for code in codes])

    def _set_target_lang(self, code):
        self._apply_target(code)
        self.post(self._flash, f'{tr("target_set")}: {target_title(code)}')

    def _apply_target(self, code):
        """Сменили язык перевода — на него же переходит и интерфейс."""
        self._save_setting("target_lang", code)
        if not load_ui_language(code):
            # надписей ещё нет: пока говорим по-английски, перевод придёт следом
            fetch_ui_language(code, done=lambda: self.post(self._refresh_ui))
        self._refresh_ui()

    def _refresh_ui(self):
        """Пересобираем то, что уже нарисовано: окна результата и меню в трее."""
        for win in list(ResultWindow._open):
            try:
                win._build(focus=False)
            except Exception:
                pass
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:
                pass

    def _theme_menu(self, pystray):
        """Светлое, тёмное или как в системе.

        «Как в Windows» спрашивает системную настройку при каждой отрисовке
        окна, поэтому вечерний переход системы в тёмную тему подхватывается сам
        — новое окно перевода откроется уже тёмным.
        """
        options = [("auto", "theme_auto"), ("light", "theme_light"),
                   ("dark", "theme_dark")]

        def item(code, key):
            return pystray.MenuItem(
                lambda _, key=key: tr(key), lambda: self._set_theme(code), radio=True,
                checked=lambda _, code=code: str(CFG.get("theme", "auto")) == code)

        return pystray.Menu(*[item(code, key) for code, key in options])

    def _set_theme(self, code):
        self._save_setting("theme", code)
        self._refresh_ui()
        titles = {"auto": "theme_auto", "light": "theme_light", "dark": "theme_dark"}
        self.post(self._flash, f'{tr("menu_theme")}: {tr(titles[code]).lower()}')

    def _translator_menu(self, pystray):
        """Подменю «Сервис перевода».

        Бесплатные точки Google живут на честном слове: у них общий лимит на
        всех, отсюда «слишком много запросов» после десятка снимков подряд.
        Свой ключ снимает лимит и даёт качество получше — у DeepL на русском
        оно заметно лучше. Бесплатные точки при этом никуда не деваются:
        остаются запасом, если платный сервис не ответил.
        """
        options = [("free", "svc_free"), ("deepl", "svc_deepl"),
                   ("google_api", "svc_google")]

        def item(code, title):
            return pystray.MenuItem(
                lambda _, title=title: tr(title), lambda: self._set_translator(code),
                radio=True,
                checked=lambda _, code=code: str(CFG.get("translator", "free")) == code)

        return pystray.Menu(
            *[item(code, title) for code, title in options],
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: tr("menu_key"), lambda: self.post(self._ask_api_key)),
        )

    def _set_translator(self, code):
        self._save_setting("translator", code)
        titles = {"free": "Google", "deepl": "DeepL", "google_api": "Google Cloud"}
        self.post(self._flash, f'{tr("service_set")}: {titles.get(code, code)}')
        # без ключа платный сервис работать не будет — спросим сразу
        if code != "free" and not str(CFG.get("api_key", "") or "").strip():
            self.post(self._ask_api_key)

    def _ask_api_key(self):
        """Окошко для ключа. Ключ лежит в config.json открытым текстом — как и
        всё остальное там; это личный файл пользователя, не общий."""
        service = str(CFG.get("translator", "free"))
        where = {"deepl": tr("key_deepl"), "google_api": tr("key_google")}.get(
            service, tr("key_pick"))

        c = theme()
        win = tk.Toplevel(self.root)
        win.title("Ключ API")
        win.configure(bg=c["bg"])
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text=tr("key_title"), bg=c["bg"], fg=c["fg"],
                 font=("Segoe UI", 12), pady=10).pack(padx=18)
        tk.Label(win, text=where, bg=c["bg"], fg=c["dim"], justify="left",
                 font=("Segoe UI", 9)).pack(padx=18, anchor="w")
        var = tk.StringVar(value=str(CFG.get("api_key", "") or ""))
        entry = tk.Entry(win, textvariable=var, width=52, bg=c["entry"], fg=c["fg"],
                         insertbackground=c["fg"], relief="flat",
                         font=("Consolas", 10))
        entry.pack(padx=18, pady=12, ipady=5)
        entry.focus_set()
        entry.select_range(0, "end")

        def save(*_):
            self._save_setting("api_key", var.get().strip())
            self._flash(tr("key_saved") if var.get().strip() else tr("key_removed"))
            win.destroy()

        row = tk.Frame(win, bg=c["bg"])
        row.pack(pady=(0, 14))
        for text, cmd, fg, bg in ((tr("save"), save, c["accent_fg"], c["accent"]),
                                  (tr("cancel"), win.destroy, c["btn"], c["hover_active"])):
            b = tk.Label(row, text=text, bg=bg, fg=fg, font=("Segoe UI", 10),
                         padx=20, pady=6, cursor="hand2")
            b.pack(side="left", padx=6)
            b.bind("<Button-1>", lambda e, cmd=cmd: cmd())
        win.bind("<Return>", save)
        win.bind("<Escape>", lambda e: win.destroy())

        win.update_idletasks()
        mx, my, mw, mh = monitor_rect_at(win.winfo_pointerx(), win.winfo_pointery())
        win.geometry("+%d+%d" % (mx + (mw - win.winfo_width()) // 2,
                                 my + (mh - win.winfo_height()) // 2))
        win.focus_force()

    def _save_setting(self, name, value):
        """Правку из трея пишем в config.json — его читают перед каждым переводом."""
        global _CFG_MTIME
        CFG[name] = value
        save_config(CFG)
        try:                       # мы сами только что переписали файл
            _CFG_MTIME = os.path.getmtime(CONFIG_PATH)
        except OSError:
            pass
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:
                pass

    def _set_ocr_langs(self, code):
        self._save_setting("ocr_langs", code)
        self.post(self._flash, f'{tr("ocr_set")}: {lang_title(code)}')

    def _bump_font(self, delta):
        """Быстрая подкрутка кегля перевода из трея — без правки config.json руками."""
        global _CFG_MTIME
        scale = _clamp(CFG.get("overlay_font_scale", 1.0), 0.5, 3.0, 1.0)
        scale = round(min(3.0, max(0.5, scale + delta)), 2)
        CFG["overlay_font_scale"] = scale
        save_config(CFG)
        try:                       # мы сами только что переписали файл — перечитывать нечего
            _CFG_MTIME = os.path.getmtime(CONFIG_PATH)
        except OSError:
            pass
        self.post(self._flash, f'{tr("font_size")}: {int(round(scale * 100))}%')

    def _toggle_autostart(self, icon=None, item=None):
        turn_on = not autostart_enabled()
        if set_autostart(turn_on):
            self.post(self._flash, tr("autostart_on") if turn_on else tr("autostart_off"))
        if icon:
            icon.update_menu()

    @staticmethod
    def _pretty(hotkey):
        return hotkey.replace("<", "").replace(">", "").replace("+", "+").title()

    def quit(self, *_):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.post(self.root.quit)

    def run(self):
        langs = available_ocr_langs()
        print("Экранный переводчик запущен.")
        print(f"  Tesseract : {TESSERACT_CMD}")
        print(f"  Языки OCR : {CFG.get('ocr_langs')}"
              + (f"   (доступны: {', '.join(sorted(langs))})" if langs else ""))
        print(f"  Автозапуск: {'включён' if autostart_enabled() else 'выключен'}")
        print(f"  {self._pretty(CFG['hotkey_capture'])} — выделить область и перевести")
        print(f"  {self._pretty(CFG['hotkey_clipboard'])} — перевести буфер обмена")
        print("  Выход — через иконку в трее или Ctrl+C в этом окне.")
        load_promo()
        fetch_promo()
        # надписи интерфейса на языке перевода: из файла мгновенно, иначе
        # подтянем в фоне — программу это не задерживает
        if not load_ui_language(ui_lang()):
            fetch_ui_language(ui_lang(), done=lambda: self.post(self._refresh_ui))
        self.start_hotkeys()
        self.start_tray()
        self.root.mainloop()


_instance_mutex = None


def already_running():
    """Вторая копия не нужна: горячие клавиши всё равно достанутся первой."""
    global _instance_mutex
    if sys.platform != "win32":
        return False
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    _instance_mutex = kernel32.CreateMutexW(None, False, "ScreenTranslator_SingleInstance")
    return kernel32.GetLastError() == ERROR_ALREADY_EXISTS


# --------------------------------------------------------------------------------------
#  Самопроверка
# --------------------------------------------------------------------------------------
def selftest():
    """Проходим всю цепочку и печатаем отчёт: пути, Tesseract, OCR, перевод.

    Нужна на чужом компьютере, куда не заглянешь: человек запускает
    «Проверка.bat» и присылает снимок консоли — по нему сразу видно, какой
    именно кусок не поднялся.
    """
    failed = []

    def step(name, check):
        try:
            print(f"[ok] {name}: {check()}")
        except Exception as e:
            print(f"[!!] {name}: {e}")
            failed.append(name)

    def check_langs():
        langs = available_ocr_langs()
        if not langs:
            raise RuntimeError("Tesseract не видит ни одного языка")
        missing = {"eng", "osd"} - langs
        if missing:
            raise RuntimeError(f"нет обязательных: {', '.join(sorted(missing))}")
        return f"{len(langs)} шт. — {', '.join(sorted(langs))}"

    def check_ocr():
        img = Image.new("RGB", (460, 96), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(FONT_CANDIDATES[0], 46)
        except OSError:
            font = ImageFont.load_default()
        draw.text((20, 22), "Hello world", font=font, fill="black")
        got = pytesseract.image_to_string(img, lang="eng").strip()
        if "hello" not in got.lower():
            raise RuntimeError(f"вместо «Hello world» прочиталось «{got}»")
        return f"«{got}»"

    def check_translate():
        result, _ = translate("Hello world", target="ru")
        if not result:
            raise RuntimeError("сервис вернул пустой ответ")
        return f"«Hello world» → «{result}»"

    def check_gui():
        """Окно, картинка в нём и иконка в трее.

        Эти три модуля программа подгружает уже на ходу, поэтому при сборке .exe
        они теряются легче всего — а заметно это становится только в тот момент,
        когда человек нажал горячую клавишу и ничего не произошло.
        """
        import pystray  # noqa: F401
        from PIL import ImageTk
        root = tk.Tk()
        try:
            root.withdraw()
            ImageTk.PhotoImage(Image.new("RGB", (8, 8), "white"))
        finally:
            root.destroy()
        try:
            from pynput import keyboard  # noqa: F401
            spare = "pynput на месте"
        except Exception:
            # запасной способ ловить горячие клавиши; основной — WinAPI
            spare = "без pynput (не страшно)"
        return f"окно рисуется, трей и {spare}"

    print("Экранный переводчик — самопроверка")
    print(f"папка программы : {APP_DIR}")
    print(f"файлы настроек  : {DATA_DIR}")
    print(f"tesseract.exe   : {TESSERACT_CMD}")
    print(f"языки OCR       : {LOCAL_TESSDATA or 'системные'}")
    print(f"временная папка : {TEMP_DIR}")
    print()

    step("Tesseract запускается", lambda: f"версия {pytesseract.get_tesseract_version()}")
    step("Языки распознавания", check_langs)
    step("Распознавание текста", check_ocr)
    step("Перевод через интернет", check_translate)
    step("Окно и иконка в трее", check_gui)

    print()
    if failed:
        print("НЕ РАБОТАЕТ: " + ", ".join(failed))
        print("Пришлите этот текст автору программы.")
        return 1
    print("ВСЁ РАБОТАЕТ. Закрывайте окно и пользуйтесь Ctrl+Alt+Z.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if already_running():
        print("Экранный переводчик уже запущен — ищите синюю иконку в трее.")
        sys.exit(0)
    try:
        App().run()
    except KeyboardInterrupt:
        pass
