# -*- coding: utf-8 -*-
"""
Пересчёт цен на экране в другие валюты — своя клавиша, по умолчанию Ctrl+Alt+D.

Переводчик про этот файл не знает, и так задумано. Он нарочно выбрасывает
строки без слов («$239.99» переводить нечего), а пересчёту нужны как раз они.
Сведи оба режима в один проход — и любая правка ради цен рисковала бы сломать
перевод или замедлить его. Поэтому здесь своё распознавание, свой разбор и своя
отрисовка, а из основного файла сюда приходит только снимок.

По порядку работы:
  1. read_lines  — распознаём область: тёмную карточку переворачиваем, бледную
                   старую цену усиливаем, стираем зачёркивания, достаём копейки,
                   набранные мелко сверху, отдаём слова с рамками;
  2. find_prices — ищем в строках «знак валюты + число»;
  3. Rates       — курсы: ЦБ РФ, а чего у него нет — запасной источник; кэш на диске;
  4. render      — рисуем пересчитанные суммы поверх цен.

Всё, что рисует окна и меню, живёт в основном файле: этому модулю Tk не нужен.
"""

import hashlib
import json
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
import zlib

import requests
import pytesseract
from pytesseract import Output
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps


# --------------------------------------------------------------------------------------
#  Валюты
# --------------------------------------------------------------------------------------
# Код -> (знак, пишется ли знак перед числом, знаков после запятой, русское
# название, английское). Знак — как суммы подписывают в самой стране: «$104»,
# но «8 795 ₽». Где своего знака нет или он путается с чужим (kr — шведская,
# норвежская и датская кроны разом), пишем код.
CURRENCIES = {
    "RUB": ("₽", False, 2, "Российский рубль", "Russian ruble"),
    "USD": ("$", True, 2, "Доллар США", "US dollar"),
    "EUR": ("€", True, 2, "Евро", "Euro"),
    "CNY": ("¥", True, 2, "Китайский юань", "Chinese yuan"),
    "KZT": ("₸", False, 2, "Казахстанский тенге", "Kazakhstani tenge"),
    "BYN": ("Br", False, 2, "Белорусский рубль", "Belarusian ruble"),
    "UAH": ("₴", False, 2, "Украинская гривна", "Ukrainian hryvnia"),
    "TRY": ("₺", False, 2, "Турецкая лира", "Turkish lira"),
    "GBP": ("£", True, 2, "Фунт стерлингов", "Pound sterling"),
    "JPY": ("¥", True, 0, "Японская иена", "Japanese yen"),
    "KRW": ("₩", True, 0, "Южнокорейская вона", "South Korean won"),
    "INR": ("₹", True, 2, "Индийская рупия", "Indian rupee"),
    "AED": ("AED", False, 2, "Дирхам ОАЭ", "UAE dirham"),
    "THB": ("฿", True, 2, "Тайский бат", "Thai baht"),
    "VND": ("₫", False, 0, "Вьетнамский донг", "Vietnamese dong"),
    "GEL": ("₾", False, 2, "Грузинский лари", "Georgian lari"),
    "AMD": ("֏", False, 0, "Армянский драм", "Armenian dram"),
    "AZN": ("₼", False, 2, "Азербайджанский манат", "Azerbaijani manat"),
    "UZS": ("UZS", False, 0, "Узбекский сум", "Uzbekistani som"),
    "KGS": ("KGS", False, 2, "Киргизский сом", "Kyrgyzstani som"),
    "TJS": ("TJS", False, 2, "Таджикский сомони", "Tajikistani somoni"),
    "CHF": ("CHF", True, 2, "Швейцарский франк", "Swiss franc"),
    "PLN": ("zł", False, 2, "Польский злотый", "Polish zloty"),
    "CZK": ("Kč", False, 2, "Чешская крона", "Czech koruna"),
    "HUF": ("Ft", False, 0, "Венгерский форинт", "Hungarian forint"),
    "RON": ("lei", False, 2, "Румынский лей", "Romanian leu"),
    "SEK": ("SEK", False, 2, "Шведская крона", "Swedish krona"),
    "NOK": ("NOK", False, 2, "Норвежская крона", "Norwegian krone"),
    "DKK": ("DKK", False, 2, "Датская крона", "Danish krone"),
    "CAD": ("C$", True, 2, "Канадский доллар", "Canadian dollar"),
    "AUD": ("A$", True, 2, "Австралийский доллар", "Australian dollar"),
    "NZD": ("NZ$", True, 2, "Новозеландский доллар", "New Zealand dollar"),
    "SGD": ("S$", True, 2, "Сингапурский доллар", "Singapore dollar"),
    "HKD": ("HK$", True, 2, "Гонконгский доллар", "Hong Kong dollar"),
    "TWD": ("NT$", True, 0, "Тайваньский доллар", "New Taiwan dollar"),
    "MYR": ("RM", True, 2, "Малайзийский ринггит", "Malaysian ringgit"),
    "IDR": ("Rp", True, 0, "Индонезийская рупия", "Indonesian rupiah"),
    "PHP": ("₱", True, 2, "Филиппинское песо", "Philippine peso"),
    "PKR": ("PKR", False, 0, "Пакистанская рупия", "Pakistani rupee"),
    "LKR": ("LKR", False, 2, "Шри-ланкийская рупия", "Sri Lankan rupee"),
    "NPR": ("NPR", False, 2, "Непальская рупия", "Nepalese rupee"),
    "ILS": ("₪", True, 2, "Израильский шекель", "Israeli shekel"),
    "SAR": ("SAR", False, 2, "Саудовский риял", "Saudi riyal"),
    "QAR": ("QAR", False, 2, "Катарский риал", "Qatari riyal"),
    "KWD": ("KWD", False, 3, "Кувейтский динар", "Kuwaiti dinar"),
    "BHD": ("BHD", False, 3, "Бахрейнский динар", "Bahraini dinar"),
    "OMR": ("OMR", False, 3, "Оманский риал", "Omani rial"),
    "JOD": ("JOD", False, 3, "Иорданский динар", "Jordanian dinar"),
    "EGP": ("EGP", False, 2, "Египетский фунт", "Egyptian pound"),
    "BRL": ("R$", True, 2, "Бразильский реал", "Brazilian real"),
    "MXN": ("MX$", True, 2, "Мексиканское песо", "Mexican peso"),
    "ARS": ("ARS", False, 2, "Аргентинское песо", "Argentine peso"),
    "CLP": ("CLP", False, 0, "Чилийское песо", "Chilean peso"),
    "COP": ("COP", False, 0, "Колумбийское песо", "Colombian peso"),
    "ZAR": ("R", True, 2, "Южноафриканский рэнд", "South African rand"),
    "RSD": ("RSD", False, 0, "Сербский динар", "Serbian dinar"),
    "MDL": ("MDL", False, 2, "Молдавский лей", "Moldovan leu"),
    "MNT": ("₮", False, 0, "Монгольский тугрик", "Mongolian tugrik"),
    "ISK": ("ISK", False, 0, "Исландская крона", "Icelandic krona"),
    "BTC": ("BTC", False, 8, "Биткоин", "Bitcoin"),
    "ETH": ("ETH", False, 8, "Эфир", "Ether"),
    "USDT": ("USDT", False, 2, "Tether (USDT)", "Tether (USDT)"),
}

# Порядок в списках выбора: сначала то, что нужно чаще, остальное — как в CURRENCIES.
POPULAR = ["RUB", "USD", "EUR", "CNY", "KZT", "BYN", "UAH", "TRY", "GBP", "JPY",
           "KRW", "INR", "AED", "THB", "VND", "GEL", "AMD", "UZS"]
CHOICES = POPULAR + [c for c in CURRENCIES if c not in POPULAR]

# В какую валюту человеку привычно считать — по языку перевода. Вторым идёт
# доллар: по нему сверяют цены в любой стране.
HOME_CURRENCY = {
    "ru": "RUB", "be": "BYN", "kk": "KZT", "uk": "UAH", "en": "USD",
    "de": "EUR", "fr": "EUR", "es": "EUR", "it": "EUR", "pt": "EUR", "nl": "EUR",
    "pl": "PLN", "cs": "CZK", "sv": "SEK", "tr": "TRY", "he": "ILS", "ar": "AED",
    "zh": "CNY", "ja": "JPY", "ko": "KRW", "hi": "INR", "id": "IDR", "vi": "VND",
}
MAX_TARGETS = 3


def default_targets(lang):
    home = HOME_CURRENCY.get(str(lang or "").lower().split("-")[0], "USD")
    return [home, "USD"] if home != "USD" else ["USD", "EUR"]


def symbol(code):
    return CURRENCIES.get(code, (code,))[0]


def currency_name(code, russian):
    info = CURRENCIES.get(code)
    if not info:
        return code
    return info[3] if russian else info[4]


# --------------------------------------------------------------------------------------
#  Знаки валют в тексте
# --------------------------------------------------------------------------------------
# Метка валюты -> (кандидаты, где стоит, слабая ли).
#
# Кандидатов несколько, когда знак общий: «$» — это и доллар США, и
# сингапурский, и австралийский. Первый в списке — что берём по умолчанию;
# выбор «валюты на экране» переключает на другой кандидат, если он в списке есть.
#
# «Где стоит»: pre — перед числом, post — после, any — где угодно.
#
# Слабые метки — это не настоящие знаки, а то, во что распознавание превращает
# редкие знаки: ₩ и ₺ оно читает как «#», ₸ как «T», ฿ как «B». Буква у
# числа слишком часто значит совсем другое («#12345» — номер заказа, «B590» —
# модель ноутбука), поэтому слабая метка работает, только когда человек сам
# назвал эту валюту «валютой на экране».
#
# Вместо «слабая ли» может стоять образец числа: метка работает, только если
# число рядом на него похоже, иначе её нет вовсе. Так устроены двойники «€» и «₹».
DOLLARS = ("USD", "SGD", "AUD", "CAD", "HKD", "NZD", "TWD", "MXN", "ARS", "CLP", "COP")
YENS = ("JPY", "CNY")
KRONAS = ("SEK", "NOK", "DKK", "ISK")
RUPEES = ("INR", "PKR", "LKR", "NPR")
_MONEY = re.compile(r"[.,]\d{2}$")                              # «79,99»
_LAKH = re.compile(r"^\d{1,2}(?:,\d\d)+,\d{3}(?:\.\d\d)?$")     # «1,23,456.00»

_MARKER_SPEC = [
    # --- доллары
    (["$", "долл.", "долл", "доллар", "доллара", "долларов", "dollars"], DOLLARS, "any", False),
    (["US$", "U$S", "$US"], ("USD",), "any", False),
    # «S$» распознавание читает как «$$»: две одинаковые буквы-змейки
    (["S$", "SG$", "$$"], ("SGD",), "any", False),
    (["A$", "AU$"], ("AUD",), "any", False),
    # «C$» мелким шрифтом распознавание нередко читает как «c$100» или «©$100»
    (["C$", "c$", "©$", "CA$", "Can$"], ("CAD",), "any", False),
    (["HK$"], ("HKD",), "any", False),
    (["NZ$"], ("NZD",), "any", False),
    # «NT$» шрифтом Calibri читается «NTS$», и выходил доллар США
    (["NT$", "NTS$"], ("TWD",), "any", False),
    (["MX$", "Mex$"], ("MXN",), "any", False),
    (["R$"], ("BRL",), "any", False),
    # --- евро, фунт, рубль
    (["€", "euro", "euros", "евро"], ("EUR",), "any", False),
    (["£"], ("GBP",), "any", False),
    (["₽", "руб", "руб.", "Руб", "Руб.", "РУБ", "рубль", "рубля", "рублей",
      # так рубли читает английская модель распознавания
      "py6", "py6.", "pyб", "pyб.", "pуб", "pуб."], ("RUB",), "any", False),
    # «₽» распознавание отдаёт латинской или русской «P», бывает с «°» спереди
    # («1299°P»), сокращение «р.» — латинской «p.». После числа это рубль;
    # перед числом та же «P» — песо, ниже.
    (["р.", "p.", "°P", "P", "Р", "р"], ("RUB",), "post", False),
    # --- рупии
    (["₹"], ("INR",), "any", False),
    # «₹» читается как «%». Но и настоящий процент перед ценой бывает — скидка
    # «-15% 1 299 ₽»; как их различаем — в _marker_before и find_prices.
    (["%"], ("INR",), "pre", False),
    (["Rs", "Rs.", "RS", "RS.", "₨", "rupees"], RUPEES, "any", False),
    (["Rp", "Rp."], ("IDR",), "any", False),
    (["RM"], ("MYR",), "any", False),
    # --- Азия
    (["¥", "yen"], YENS, "any", False),
    (["元", "RMB", "CN¥", "юань", "юаня", "юаней", "yuan"], ("CNY",), "any", False),
    (["円", "JP¥"], ("JPY",), "any", False),
    (["₩", "원"], ("KRW",), "any", False),
    (["฿", "บาท"], ("THB",), "any", False),
    (["₫", "đ", "vnđ", "VNĐ"], ("VND",), "any", False),
    (["₱"], ("PHP",), "any", False),
    (["₪", "NIS"], ("ILS",), "any", False),
    (["Dhs", "Dhs.", "Dh", "د.إ"], ("AED",), "any", False),
    (["ر.س"], ("SAR",), "any", False),
    (["E£"], ("EGP",), "any", False),
    # --- СНГ и рядом
    (["₸", "тг", "тг.", "тенге"], ("KZT",), "any", False),
    # так английская модель читает «тг», «драм», «сум», «сом» жирным шрифтом
    (["tr"], ("KZT",), "post", False),
    (["apam"], ("AMD",), "post", False),
    (["cym"], ("UZS",), "post", False),
    (["com"], ("KGS",), "post", False),
    (["₴", "грн", "грн.", "гривен"], ("UAH",), "any", False),
    (["Br", "бел. руб.", "бел.руб."], ("BYN",), "any", False),
    (["₾", "лари"], ("GEL",), "any", False),
    (["֏", "драм"], ("AMD",), "any", False),
    (["₼", "ман.", "манат"], ("AZN",), "any", False),
    (["сум", "сўм", "so'm"], ("UZS",), "any", False),
    (["сом"], ("KGS",), "any", False),
    (["сомони"], ("TJS",), "any", False),
    (["₺", "TL"], ("TRY",), "any", False),
    # --- Европа
    (["zł", "zl", "zt", "zi"], ("PLN",), "post", False),
    (["Kč", "Ke", "KC"], ("CZK",), "post", False),
    (["Ft"], ("HUF",), "post", False),
    (["lei"], ("RON",), "post", False),
    (["kr", "kr.", "Kr", "Kr."], KRONAS, "any", False),
    (["Fr.", "SFr.", "fr."], ("CHF",), "any", False),
    (["дин.", "din."], ("RSD",), "post", False),
    (["₮"], ("MNT",), "any", False),
    (["₿"], ("BTC",), "any", False),
    (["USDC"], ("USDT",), "any", False),
    # --- двойники: во что распознавание превращает «€» и «₹». Сами по себе
    # эти значки о деньгах не говорят («© 2024», «~15 мин»), поэтому верим им,
    # только когда число рядом записано как деньги: с копейками («79,99 ©»)
    # или индийскими разрядами по два («~1,23,456.00» — так пишут только рупии)
    (["©"], ("EUR",), "any", _MONEY),
    (["E"], ("EUR",), "post", _MONEY),
    (["~", "<", "X"], ("INR",), "pre", _LAKH),
    # --- слабые: что остаётся от редких знаков после распознавания
    (["#"], ("KRW", "TRY"), "pre", True),
    (["T", "Т"], ("KZT",), "post", True),
    (["B"], ("THB",), "pre", True),
    (["P", "Р"], ("PHP",), "pre", True),
    (["@"], ("GEL",), "pre", True),
    (["R"], ("ZAR",), "pre", True),
]

# Латинские буквы, у которых есть русские двойники. Смешанный набор языков
# читает «A$» как «А$» с русской «А», и знак переставал узнаваться.
_CYR_TWIN = str.maketrans("ABCEHKMOPTXacepoxy", "АВСЕНКМОРТХасероху")
_LATIN_TWIN = str.maketrans("АВСЕНКМОРТХасероху", "ABCEHKMOPTXacepoxy")


def _build_markers():
    markers = {}
    for tokens, codes, where, weak in _MARKER_SPEC:
        for token in tokens:
            for variant in {token, token.translate(_CYR_TWIN)}:
                # первое написание главнее: «P» после числа — рубль, а не песо,
                # поэтому у одного написания может быть по записи на каждую сторону
                for side in (("pre", "post") if where == "any" else (where,)):
                    markers.setdefault((variant, side), (codes, weak))
    # Код валюты сам по себе — тоже метка: «USD 50», «50EUR», «CHF 49.–».
    for code in CURRENCIES:
        for side in ("pre", "post"):
            markers.setdefault((code, side), ((code,), False))
    # советское «RUR» ещё встречается в прайсах
    for side in ("pre", "post"):
        markers.setdefault(("RUR", side), (("RUB",), False))
    return markers


MARKERS = _build_markers()
_PRE = sorted({t for t, s in MARKERS if s == "pre"}, key=len, reverse=True)
_POST = sorted({t for t, s in MARKERS if s == "post"}, key=len, reverse=True)

# Множители после числа: «$1.2M», «12,5 тыс. ₽». Два последних написания в
# каждой строке — как распознавание читает русские «тыс.» и «млн» без
# русской модели: «Thic.» и «MAH».
MULTIPLIERS = [
    (["thousand", "тыс.", "тыс", "Thic.", "Thic", "k", "K"], 1e3),
    (["million", "Million", "млн.", "млн", "MAH", "mln", "mn", "MM", "M"], 1e6),
    (["billion", "Billion", "млрд.", "млрд", "bn", "B"], 1e9),
]
_MULT = sorted(((t, k) for tokens, k in MULTIPLIERS for t in tokens),
               key=lambda tk: len(tk[0]), reverse=True)

# Одинаковой длины замены: по номеру знака в тексте находим его место в слове,
# поэтому длина строки после чистки меняться не должна.
_NORM = str.maketrans({
    "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u2007": " ", "\u2002": " ",
    "\u2003": " ", "\u2008": " ",
    "’": "'", "‘": "'", "`": "'", "´": "'",
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "，": ",", "．": ".", "＄": "$", "￥": "¥", "￡": "£", "＃": "#",
    **{chr(0xFF10 + i): str(i) for i in range(10)},
})

# Число: цифры с разделителями разрядов и дробной частью. Пробел — разделитель
# разрядов, поэтому внутри числа он только перед ровно тремя цифрами:
# «1 299,99» — одно число, а «4.8 0 (1,234» — три разных.
_NUM = re.compile(r"\d(?:\d|[.,'](?=\d)| (?=\d{3}(?!\d)))*")
# Метки с цифрой внутри — «py6», так английская модель читает «руб»:
# (метка, где в ней цифры начинаются, где кончаются)
_DIGIT_MARKS = []
for _token in sorted({t for t, _ in MARKERS}):
    _digits = re.search(r"\d+", _token)
    if _digits:
        _DIGIT_MARKS.append((_token, _digits.start(), _digits.end()))
_SWISS_TAIL = re.compile(r"^[.,]-{1,2}")      # «49.–», «49,-»: без копеек

# Что стоит перед числом и делает его не ценой: номер, счётчик, код, знаменатель
# («₹1,100.00 /100 g» — за сто граммов, а не сто рупий).
_NOT_PRICE_BEFORE = re.compile(r"(?:#|№|No\.?|x|×|\*|v|V|ver\.?|/)\s?$")
# Единицы после числа: у цены их не бывает.
_UNITS_AFTER = re.compile(
    r"^\s?(?:%|°|x\b|×|/5|★|шт|pcs|pc\b|reviews?|ratings?|отзыв|оцен|sold|продан|"
    r"cm\b|mm\b|m\b|km\b|kg\b|g\b|gb\b|GB\b|MB\b|TB\b|mAh|W\b|V\b|Hz|"
    r"см\b|мм\b|м\b|км\b|кг\b|г\b|л\b|мл\b|ml\b|мин|min\b|h\b|ч\b|лет|years?|дн)",
    re.IGNORECASE)
# Кольцо между числами диапазона: «$10–20», «от 1 500 до 3 000 ₽», «10 to 20 €».
# «go», «no» и «fo» — так «до» читает английская модель распознавания.
_RANGE_LINK = re.compile(r"^\s?(?:-|~|to|до|go|no|fo)\s?$", re.IGNORECASE)

_KANA = re.compile(r"[\u3040-\u30ff]")
_HAN = re.compile(r"[\u4e00-\u9fff]")


def _parse_number(raw, code_hint=None):
    """«1.299,99» -> 1299.99. Не число — ValueError.

    Главный вопрос — какой разделитель дробный. Последний разделитель с одной-
    двумя цифрами за ним дробный всегда. С тремя — разрядный («1,234 $»,
    «Rp 150.000»), кроме валют с тремя знаками после запятой (кувейтский
    динар: «KWD 12.500» — это двенадцать с половиной) и чисел вида «0.005».
    Индийская запись «1,23,456.00» разбирается тем же правилом: всё, что
    левее дробного разделителя, — разряды, какой бы длины ни были группы.
    """
    s = raw.strip()
    seps = [(i, ch) for i, ch in enumerate(s) if ch in ".,' "]
    if not seps:
        return float(s)
    digits_after = len(s) - seps[-1][0] - 1
    last = seps[-1][1]
    decimals3 = code_hint and CURRENCIES.get(code_hint, (None, None, 2))[2] == 3
    int_part = s[:seps[-1][0]].replace(".", "").replace(",", "").replace("'", "").replace(" ", "")
    if last in ".,":
        others = {ch for _, ch in seps[:-1]}
        if digits_after != 3:
            decimal = True
        elif others:
            # «1,234,567» — все разделители одинаковые: разряды; «1.234,567» — нет
            decimal = last not in others
        else:
            decimal = bool(decimals3) or int_part == "0"
        if decimal:
            frac = s[seps[-1][0] + 1:]
            return float(f"{int_part or '0'}.{frac}")
    return float(s.replace(".", "").replace(",", "").replace("'", "").replace(" ", ""))


def _numbers(text):
    """Числа в строке: (начало, конец) каждого.

    Число, начатое цифрой метки, на этой цифре и кончается: в «1 299 py6 999 руб»
    пробел перед тремя цифрами склеивал «6» из «py6» с 999 в «6 999», это число
    отбрасывалось как слово, и цена 999 пропадала. Теперь «6» — отдельное число,
    как в «py6. 999», а 999 — своё.
    """
    pos = 0
    while True:
        m = _NUM.search(text, pos)
        if not m:
            return
        start, end = m.span()
        # Пробел делит разряды, только если перед ним не больше трёх цифр:
        # «1299 999 ₽» — старая цена и новая, а не 1 299 999
        head = re.match(r"\d+", text[start:end]).end()
        if head > 3 and text[start + head:start + head + 1] == " ":
            end = start + head
        for token, d0, d1 in _DIGIT_MARKS:
            at = start - d0
            # метка стоит отдельно, как в _marker_before: «copy6» — не «py6»
            if at >= 0 and text.startswith(token, at) \
                    and not (at and text[at - 1].isalpha()):
                end = min(end, at + d1)
                break
        yield start, end
        pos = end


def _marker_before(text, pos):
    """Метка валюты вплотную перед числом (можно через пробел): (метка, начало) или None."""
    left = text[:pos]
    glued = not left.endswith(" ")
    if not glued:
        left = left[:-1]
    for token in _PRE:
        if left.endswith(token):
            start = len(left) - len(token)
            prev = left[start - 1] if start > 0 else " "
            # буквенная метка должна стоять отдельно: «iPhoneUSD» — не доллары
            if token[0].isalpha() and prev.isalpha():
                continue
            # «%» за числом — процент скидки, а не «₹»: «-15% 1 299 ₽», «-15 % 1299».
            # Но «₹» прижат к своей цене, и «MRP ₹1,999 ₹999» читается
            # как «MRP 21,999 %999» — там «%» за числом всё-таки рупия.
            if token == "%" and re.search(r"\d$" if glued else r"\d ?$", left[:start]):
                continue
            return token, start
    return None


def _marker_after(text, pos):
    """Метка сразу после числа: (метка, конец) или None."""
    right = text[pos:]
    skip = 1 if right.startswith(" ") else 0
    right = right[skip:]
    for token in _POST:
        if right.startswith(token):
            nxt = right[len(token):len(token) + 1]
            if token[-1].isalpha() and nxt.isalpha():
                continue
            return token, pos + skip + len(token)
    return None


def _multiplier_after(text, pos):
    right = text[pos:]
    skip = 1 if right.startswith(" ") else 0
    right = right[skip:]
    for token, k in _MULT:
        if right.startswith(token):
            nxt = right[len(token):len(token) + 1]
            if token[-1].isalpha() and nxt.isalpha():
                continue
            # «₹1,499 M.R.P.: ₹2,999»: «M.» с буквой за точкой — сокращение, а не
            # миллионы; выходило полтора миллиарда рупий
            if token[-1].isalpha() and re.match(r"\.[^\W\d_]", right[len(token):]):
                continue
            return k, pos + skip + len(token)
    return None


def _resolve(codes, weak, source, context):
    """Метка -> код валюты с учётом выбранной «валюты на экране» и соседнего текста."""
    if source and source != "auto" and source in codes:
        return source
    if weak:
        return None                 # слабая метка без явного выбора не работает
    if codes == YENS:
        # «¥» пишут и японцы, и китайцы. Кана на снимке — значит, Япония;
        # одни иероглифы — Китай (AliExpress, Taobao); без них — по умолчанию иена.
        if _KANA.search(context):
            return "JPY"
        if _HAN.search(context):
            return "CNY"
    return codes[0]


def _looks_like_price(text, start, end, value):
    """Число без знака валюты — цена ли это. Нужно, только когда валюта выбрана вручную.

    Без знака ценой может быть что угодно: рейтинг «4.8», «1,234 отзыва»,
    номер заказа. Поэтому берём только то, что по записи похоже на деньги:
    с копейками, с разрядами или одно само по себе на строке.
    """
    before, after = text[:start], text[end:]
    if _NOT_PRICE_BEFORE.search(before) or _UNITS_AFTER.match(after):
        return False
    raw = text[start:end]
    if re.search(r"[.,]\d{2}$", raw) or re.search(r"\d[ ,.']\d{3}", raw):
        return True
    alone = not re.search(r"[^\W\d_]", before + after)
    if alone and value >= 10 and not (1900 <= value <= 2100 and raw.isdigit()):
        return True
    return False


def _sign_goes_right(left, right):
    """Знак стоит между двумя числами — чей он: True — правого, False — левого.

    `left` и `right` — метки обоих чисел; спорный знак у левого — «post»,
    у правого — «pre». По порядку:
      - прижат к одному числу, от другого отделён пробелом — того, к кому
        прижат: «2499 $14.99» (зачёркнутую «$24.99» распознавание прочло без
        знака и точки) — доллар у 14.99, а 2499 остаётся без знака;
      - у одного числа свой знак уже есть с дальней стороны — спорный знак
        другому: «40 $ 25 $», «$ 40 $ 25»;
      - иначе — по тому, с какой стороны эту валюту пишут: «₽» за числом, «$»
        перед ним. Так в «1 299 ₽ 999» рубль у 1 299, а 999 — число без знака.
    """
    sign, same = left["post"], right["pre"]
    if sign["glued"] != same["glued"]:
        return same["glued"]
    if "pre" in left and "post" not in right:
        return True
    if "post" in right and "pre" not in left:
        return False
    return CURRENCIES[sign["code"]][1]


class Price:
    """Найденная цена: сколько, в чём, где на картинке и зачёркнута ли."""

    def __init__(self, amount, code, text, box, strike=False, weak=False, short=False):
        self.amount = amount
        self.code = code
        self.text = text          # как было написано на экране
        self.box = box            # (x0, y0, x1, y1) в пикселях снимка
        self.strike = strike      # старая цена, перечёркнутая
        self.weak = weak          # валюта не по знаку, а по выбору человека
        self.short = short        # записана с множителем: «$1.2M», «12,5 тыс. ₽»
        self.digits_x = (box[0], box[2])   # где по x стоят сами цифры, без знака валюты

    def __repr__(self):
        return f"Price({self.amount:g} {self.code}, {self.text!r}{', old' if self.strike else ''})"


def find_prices(lines, source="auto"):
    """Цены в распознанных строках. `lines` — из read_lines.

    `source` — «валюта на экране»: "auto" или код. Явный знак главнее выбора:
    «€» всегда евро, какую бы валюту ни выбрали. Выбор решает только спорное:
    общий знак («$», «¥», «kr»), слабые метки и числа без знака вовсе.
    """
    context = " ".join(w["text"] for line in lines for w in line)
    found = []
    for line in lines:
        text, spans = _line_text(line)
        norm = text.translate(_NORM)
        nums = []
        taken = 0                   # до сюда строка уже разобрана: копейки «19€99»
        for start, end in _numbers(norm):
            prev = norm[start - 1] if start else " "
            if prev.isdigit() or start < taken:
                continue
            # хвост «.–» у швейцарских цен — часть числа, а не минус
            tail = _SWISS_TAIL.match(norm[end:])
            pre = _marker_before(norm, start)
            after = end + (tail.end() if tail else 0)
            mult = _multiplier_after(norm, after)
            post_at = mult[1] if mult else after
            post = _marker_after(norm, post_at)
            # «Qty 2 $14.99», «Size 10 $49.99»: знак через пробел, но вплотную к
            # следующему числу — это его знак, а само число — количество или
            # размер, не цена. «14,99 $» — по-старому, слитное «19€99» — ниже.
            if post and norm[post_at:post_at + 1] == " " \
                    and norm[post[1]:post[1] + 1].isdigit() and _marker_before(norm, post[1]):
                if not pre:
                    continue
                post = None
            # «19€99» — так пишут во Франции и Бельгии: знак стоит на месте
            # запятой, и две цифры за ним — копейки этой же цены, а не вторая
            # цена «€99». Только знак, вплотную с обеих сторон, и ровно две цифры.
            raw, cents_end = norm[start:end], None
            if post and not pre and post_at == end and post[1] == end + len(post[0]) \
                    and not any(ch.isalnum() for ch in post[0]) \
                    and not re.search(r"[.,]\d{1,2}$", raw):
                cents = _NUM.match(norm, post[1])
                if cents and len(cents.group()) == 2:
                    raw = f"{raw}.{cents.group()}"
                    cents_end = taken = cents.end()
            # буква вплотную к числу без пробела — это слово («iPhone15»), если
            # только сама буква не метка («USD50», «Rs.9»). Ценой слово не станет,
            # но знак рядом может быть его: зачёркнутая «1299 руб.» читается как
            # «i299 py6.», и её рубль не должен достаться следующей цене
            word = prev.isalpha() and not pre
            # метки, которые что-то значат: слабая без выбора валюты — не метка
            marks = {}
            for found_marker, side in ((pre, "pre"), (post, "post")):
                if not found_marker:
                    continue
                token, edge = found_marker
                codes, is_weak = MARKERS[(token, side)]
                if hasattr(is_weak, "search"):      # двойник знака: смотря какое число
                    if not is_weak.search(raw):
                        continue
                    is_weak = False
                resolved = _resolve(codes, is_weak, source, context)
                if not resolved:
                    continue
                if side == "pre":
                    at, glued = (edge, edge + len(token)), edge + len(token) == start
                else:
                    at, glued = (edge - len(token), edge), edge - len(token) == post_at
                if side == "post" and cents_end:
                    at = (at[0], cents_end)             # копейки — в рамку цены
                marks[side] = {"token": token, "codes": codes, "code": resolved,
                               "weak": is_weak, "at": at, "glued": glued}
            nums.append({"start": start, "end": end, "after": after, "post_at": post_at,
                         "mult": mult, "marks": marks, "word": word, "raw": raw})

        # Знак между двумя числами — одного из них, а не обоих: иначе «2499 $14.99»
        # давало две цены в долларах, а у «999 ₽ 1 299 ₽» рамка второй цены
        # наезжала на первую. Соседей ищем по месту знака, а не по порядку:
        # между ними может оказаться цифра из самой метки — «6» из «py6.».
        for i, b in enumerate(nums):
            for a in nums[:i]:
                post, pre = a["marks"].get("post"), b["marks"].get("pre")
                if post and pre and post["at"][0] < pre["at"][1] and pre["at"][0] < post["at"][1]:
                    if post["glued"] and pre["glued"] and not post["token"][0].isalnum() \
                            and "pre" not in a["marks"] and "post" not in b["marks"]:
                        # Единственный знак вплотную между цифрами, и это не копейки
                        # «19€99»: так не пишут, это мусор распознавания. Мелкая
                        # зачёркнутая «₹549.00» читалась «7£49.00» — выходило 49 фунтов.
                        del a["marks"]["post"], b["marks"]["pre"]
                    elif _sign_goes_right(a["marks"], b["marks"]):
                        del a["marks"]["post"]
                    else:
                        del b["marks"]["pre"]

        items = []
        for num in nums:
            if num["word"]:
                continue
            start, end, marks, mult = num["start"], num["end"], num["marks"], num["mult"]
            raw = num["raw"]
            code, weak, span0, span1 = None, False, start, num["post_at"]
            pick = None
            for side in ("pre", "post"):
                mark = marks.get(side)
                if not mark:
                    continue
                # «$29.99 USD»: общий знак спереди, точный код сзади — верим коду.
                # «%1 299 ₽»: «%» — лишь догадка, что это испорченный «₹»,
                # а знак сзади настоящий — верим знаку
                rank = (mark["token"] != "%", len(mark["codes"]) == 1)
                if pick is None or rank > pick[0]:
                    pick = (rank, mark)
            if pick:
                code, weak = pick[1]["code"], pick[1]["weak"]
                # Рамка закрывает выбранный знак и второй, если он про ту же
                # валюту: «$29.99 USD» — целиком, а от «%1299 ₽» — только «1299 ₽».
                for side, mark in marks.items():
                    if mark is pick[1] or code in mark["codes"]:
                        if side == "pre":
                            span0 = mark["at"][0]
                        else:
                            span1 = mark["at"][1]
            elif mult:
                mult = None             # «10 MB»: без валюты это не множитель
                span1 = num["after"]
            try:
                value = _parse_number(raw, code)
            except ValueError:
                continue
            # Дробная часть длиннее, чем бывает у денег, — мусор распознавания:
            # «12,900원» читается как «12,9000», и с выбранной воной выходило 12,9.
            # Лучше не показать цену, чем показать неверную.
            frac = re.search(r"[.,](\d+)$", raw)
            places = CURRENCIES.get(code, (None, None, 2))[2] if code else 2
            if frac and len(frac.group(1)) != 3 and len(frac.group(1)) > max(2, places):
                continue
            if mult:
                value *= mult[0]
            items.append({"start": start, "end": end, "span": (span0, span1), "code": code,
                          "weak": weak, "value": value, "raw": raw, "short": bool(mult)})

        # Числа без знака. «от 1 500 до 3 000 ₽», «$10–20»: знак один на двоих,
        # и безымянное число берёт его у соседа. Если сосед по величине совсем
        # другой — это не диапазон: «#12345 - $45.00» — номер заказа и цена.
        for i, it in enumerate(items):
            if it["code"]:
                continue
            for j in (i + 1, i - 1):
                if not 0 <= j < len(items) or not items[j]["code"]:
                    continue
                a, b = (it, items[j]) if j > i else (items[j], it)
                between = norm[a["span"][1]:b["span"][0]]
                ratio = it["value"] / items[j]["value"] if items[j]["value"] else 0
                if _RANGE_LINK.match(between) and 0.05 <= ratio <= 20 \
                        and not _NOT_PRICE_BEFORE.search(norm[:it["start"]]):
                    it["code"] = items[j]["code"]
                    break
            # валюта на экране выбрана вручную — безымянное число, похожее на
            # цену, считается в ней
            if not it["code"] and source and source != "auto" \
                    and _looks_like_price(norm, it["start"], it["end"], it["value"]):
                it["code"], it["weak"] = source, True

        for it in items:
            if not it["code"] or it["value"] <= 0:
                continue
            # зачёркнутая цена, цифры которой с картинкой не сошлись (_check_struck)
            if any(w.get("doubt") for s, e, w in spans if s < it["end"] and it["start"] < e):
                continue
            span0, span1 = it["span"]
            box, strike, digits_x = _span_box(line, spans, span0, span1)
            price = Price(it["value"], it["code"], text[span0:span1].strip(), box,
                          strike, it["weak"], it["short"])
            price.digits_x = digits_x
            found.append(price)
    return found


def _line_text(line):
    """Слова строки -> (текст через пробел, [(начало, конец, слово)])."""
    parts, spans, pos = [], [], 0
    for w in line:
        if parts:
            pos += 1
        spans.append((pos, pos + len(w["text"]), w))
        parts.append(w["text"])
        pos += len(w["text"])
    return " ".join(parts), spans


def _span_box(line, spans, a, b):
    """Рамка куска строки [a, b) -> (рамка, зачёркнута ли, где по x сами цифры).

    Tesseract отдаёт рамки слов, а не букв. Если цена — часть слова
    («+$5.00», «$4.99/mo»), место внутри слова делим по ширине букв в обычном
    шрифте: поровну по буквам выходило мимо — косая черта и точка узкие, и
    «/mo» разрезало пополам.

    Высоту берём у самого длинного числа в цене, а не у всей рамки: знак
    «元» выше цифр, а рамка одинокой «1» у Tesseract бывает выше соседних
    «500» на треть — и сумма рисовалась крупнее оригинала.
    """
    x0, x1 = 10 ** 9, -1
    dx0, dx1 = 10 ** 9, -1
    strike = False
    main = None
    for s, e, w in spans:
        lo, hi = max(a, s), min(b, e)
        if lo >= hi:
            continue
        wx0, wy0, wx1, wy1 = w["box"]
        text = w["text"]
        full = _measure(text) or 1
        cx0 = wx0 + (wx1 - wx0) * _measure(text[:lo - s]) / full
        cx1 = wx0 + (wx1 - wx0) * _measure(text[:hi - s]) / full
        # «руб.» распознаётся как «py6.20»: цифры, прилипшие к знаку после
        # числа, — не хвост слова, а те же буквы, прочитанные лишний раз. По
        # ширине букв рамка кончалась на «6», и «б.» торчало из-под заливки
        if text[hi - s:].isdigit() and not text[hi - s - 1].isdigit():
            cx1 = wx1
        x0, x1 = min(x0, cx0), max(x1, cx1)
        for k in range(lo, hi):
            if text[k - s].isdigit():
                dx0 = min(dx0, wx0 + (wx1 - wx0) * _measure(text[:k - s]) / full)
                dx1 = max(dx1, wx0 + (wx1 - wx0) * _measure(text[:k - s + 1]) / full)
        strike = strike or w.get("strike", False)
        digits = sum(ch.isdigit() for ch in text[lo - s:hi - s])
        if main is None or digits > main[0]:
            main = (digits, wy0, wy1)
    if x1 < 0:
        return (0, 0, 1, 1), False, (0, 1)
    if dx1 < 0:
        dx0, dx1 = x0, x1
    return ((int(x0), int(main[1]), int(round(x1)), int(main[2])), strike,
            (int(dx0), int(round(dx1))))


def _measure(text):
    """Ширина текста в условных единицах — для деления рамки слова по буквам."""
    return _font(20, False).getlength(text) if text else 0


# --------------------------------------------------------------------------------------
#  Распознавание
# --------------------------------------------------------------------------------------
def _is_dark(gray):
    hist = gray.histogram()
    return sum(hist[:128]) > sum(hist[128:])


def _strip_frame(gray):
    """Полоса страницы вокруг карточки — закрашиваем её фоном самой карточки.

    Тёмную карточку («$14.99» в магазине игр) обводят с запасом, и в снимок
    попадает светлая страница вокруг. Распознавание видит светлую рамку с
    тёмным пятном внутри и не читает в пятне ничего. Рамку узнаём по краям:
    строки и столбцы от края внутрь, почти целиком непохожие на фон середины.
    Строка текста такой не бывает — между буквами всегда виден фон.
    """
    w, h = gray.size
    if w < 16 or h < 16:
        return gray
    hist = gray.crop((w // 5, h // 5, w - w // 5, h - h // 5)).histogram()
    bg = max(range(256), key=hist.__getitem__)
    data = gray.point(lambda v: 255 if abs(v - bg) > 60 else 0).tobytes()

    def framed(line, size):
        return line.count(255) >= 0.9 * size

    top, bottom, left, right = 0, h, 0, w
    while top < h // 3 and framed(data[top * w:(top + 1) * w], w):
        top += 1
    while bottom > h - h // 3 and framed(data[(bottom - 1) * w:bottom * w], w):
        bottom -= 1
    while left < w // 3 and framed(data[left::w], h):
        left += 1
    while right > w - w // 3 and framed(data[right - 1::w], h):
        right -= 1
    if (top, bottom, left, right) == (0, h, 0, w):
        return gray
    out = Image.new("L", (w, h), bg)
    out.paste(gray.crop((left, top, right, bottom)), (left, top))
    return out


# Насколько тёмная самая тёмная точка рядом (от фона, 0–255) -> во сколько раз
# усилить чернила. Слабее 60 — рамки и тени, а не буквы. От 165 — буквы и так
# тёмные: тонкие штрихи чёрного мелкого шрифта после увеличения бывают как
# раз такими, и усиливать их нельзя — распознавание начинало путать «5» и «S».
_BOOST_BANDS = [(60, 100, 2.5), (100, 120, 2.3), (120, 140, 2.0), (140, 165, 1.7)]


def _boost_faint(gray):
    """Бледно-серые буквы рядом с чёрными — такими же тёмными, как чёрные.

    Старую цену магазины пишут светло-серым, новую — чёрным или красным.
    Общая растяжка контраста равняется по чёрной, и серая остаётся серой:
    точка в «$29.99» у мелкого шрифта выходила бледным пятнышком, и
    распознавание читало «$2999», а бледную линию зачёркивания не находило.
    Поэтому усиливаем каждую точку по самой тёмной точке в её окрестности:
    у чёрных букв она чёрная, и они не меняются вместе со своей каймой, а
    серые темнеют до чёрного. Общее усиление всей картинки серым тоже
    помогало, но портило чёрные: «50EUR» читалось как «SOEUR».
    """
    ink = ImageOps.invert(gray)                 # чернила светлые, фон тёмный
    hist = ink.histogram()
    bg = max(range(256), key=hist.__getitem__)
    # Самое тёмное в окрестности ~9×9. Окно шире задевало места, где бледная
    # линия зачёркивания пересекает бледную цифру: там темнее, и линия рядом
    # переставала усиливаться. Считаем на копии втрое меньше: на снимке во
    # весь экран фильтр по полной картинке шёл почти секунду.
    # Уменьшаем не усреднением, а максимумом по клеткам 3×3 — из девяти
    # выборок каждой третьей точки со сдвигом, — и тонкий штрих не бледнеет.
    w, h = ink.size
    w3, h3 = -(-w // 3) * 3, -(-h // 3) * 3
    pad = ink.crop((-1, -1, w3 + 2, h3 + 2))
    peak = None
    for dy in range(3):
        for dx in range(3):
            part = pad.resize((w3 // 3, h3 // 3), Image.NEAREST, box=(dx, dy, dx + w3, dy + h3))
            peak = part if peak is None else ImageChops.lighter(peak, part)
    peak = peak.filter(ImageFilter.MaxFilter(3))
    peak = peak.resize((w3, h3), Image.NEAREST).crop((0, 0, w, h))
    out = ink
    for lo, hi, gain in _BOOST_BANDS:
        mask = peak.point(lambda v: 255 if lo <= v - bg < hi else 0)
        if mask.getbbox():
            out = Image.composite(
                ink.point(lambda v: min(255, bg + int(max(0, v - bg) * gain))), out, mask)
    return ImageOps.invert(out)


def erase_strikes(gray, min_len, max_thick):
    """Стираем тонкие горизонтальные линии: зачёркивание старой цены и подчёркивания.

    Зачёркнутую «$29.99» распознавание читает как «52933»: линия режет цифры
    посередине. Стираем её, но оставляем точки, где линию пересекает штрих
    цифры — выше и ниже линии там тоже чернила. Иначе вместе с линией
    пропадала середина «8» и «9». Возвращаем (чистую картинку, линии).

    Толщина ограничена `max_thick`: сплошная тёмная плашка — кнопка с белой
    ценой — тоже «длинная горизонтальная полоса», и стёртой вместе с ней
    пропала бы сама цена.

    Отрезки ищем регулярным выражением по байтам строки, а не перебором
    пикселей: на снимке во весь экран это 0.9 с против сотой доли.
    """
    w, h = gray.size
    dark = _is_dark(gray)
    mask = gray.point(lambda v: 255 if (v > 128) == dark else 0)   # чернила — 255
    data = mask.tobytes()
    # Линию собираем из кусков: там, где её пересекает штрих цифры, от
    # сглаживания в ней бывает просвет в пиксель-два. Целиком она тогда не
    # находилась, и зачёркнутая «$1,299.99» не считалась старой ценой.
    gap = 2
    piece_re = re.compile(rb"\xff{%d,}" % max(2, int(min_len) // 4))
    runs = []
    for y in range(h):
        base = y * w
        row = []
        for m in piece_re.finditer(data, base, base + w):
            a, b = m.start() - base, m.end() - base
            if row and a - row[-1][1] <= gap:
                row[-1][1] = b
            else:
                row.append([a, b, y, y])
        runs += [r for r in row if r[1] - r[0] >= min_len]
    # соседние строки одной линии — одна линия толщиной в несколько пикселей
    lines = []
    for run in runs:
        for ln in lines:
            if ln[3] == run[2] - 1 and \
                    min(ln[1], run[1]) - max(ln[0], run[0]) > 0.8 * (run[1] - run[0]):
                ln[0], ln[1], ln[3] = min(ln[0], run[0]), max(ln[1], run[1]), run[2]
                break
        else:
            lines.append(run)
    thin = [ln for ln in lines if ln[3] - ln[2] + 1 <= max_thick]
    if not thin:
        return gray, []
    out = gray.copy()
    po = out.load()
    bg = 0 if dark else 255
    halo = gray.point(lambda v: 255 if (v > 60 if dark else v < 195) else 0).tobytes()
    for x0, x1, y0, y1 in thin:
        # Размытый край линии стираем вместе с ней. Браузер кладёт линию не
        # по целым пикселям, и бледная кайма над и под ней оставалась на
        # снимке второй линией, а пересечения со штрихами цифр проверялись по
        # кайме, а не по самой цифре.
        while y0 > 0 and _halo_row(halo, w, y0 - 1, x0, x1):
            y0 -= 1
        while y1 + 1 < h and _halo_row(halo, w, y1 + 1, x0, x1):
            y1 += 1
        for x in range(x0, x1):
            above = y0 > 0 and data[(y0 - 1) * w + x]
            below = y1 + 1 < h and data[(y1 + 1) * w + x]
            if above and below:
                continue
            for y in range(y0, y1 + 1):
                po[x, y] = bg
    return out, [(x0, x1, y0, y1) for x0, x1, y0, y1 in thin]


def _halo_row(halo, w, y, x0, x1):
    """Строка над (под) линией — её размытый край: бледные чернила почти во всю длину."""
    return halo[y * w + x0:y * w + x1].count(255) >= 0.85 * (x1 - x0)


def _raised_tail(gray, box):
    """Копейки, набранные мелко сверху («$19⁹⁹»): где они начинаются по x, или None.

    Делим слово на знаки по пустым столбцам и смотрим на низ каждого. Обычные
    цифры стоят на одной линии, копейки висят выше неё. Распознавание их то
    теряет («$19°»), то пишет вплотную («$1999» — в сто раз дороже).
    """
    x0, y0, x1, y1 = box
    crop = gray.crop((x0, y0, x1, y1))
    w, h = crop.size
    if w < 8 or h < 16:
        return None
    dark = _is_dark(crop)
    px = crop.load()
    cols = []
    for x in range(w):
        ys = [y for y in range(h) if (px[x, y] > 128) == dark]
        cols.append((min(ys), max(ys)) if ys else None)
    glyphs, cur = [], None
    for x, c in enumerate(cols):
        if c is None:
            if cur:
                glyphs.append(cur)
                cur = None
            continue
        if cur is None:
            cur = [x, x, c[0], c[1]]
        else:
            cur[1], cur[2], cur[3] = x, min(cur[2], c[0]), max(cur[3], c[1])
    if cur:
        glyphs.append(cur)
    if len(glyphs) < 3:
        return None
    base = sorted(g[3] for g in glyphs)[len(glyphs) // 2]
    tall = max(g[3] - g[2] for g in glyphs)
    tail = []
    for g in reversed(glyphs):
        # висит: низ заметно выше общей линии, и сам знак мельче обычной цифры
        if g[3] < base - 0.3 * tall and (g[3] - g[2]) < 0.8 * tall:
            tail.append(g)
        else:
            break
    if not tail or len(tail) == len(glyphs):
        return None
    return x0 + tail[-1][0]


def _read_digits(gray, box, seps=False):
    """Прочитать цифры в рамке отдельно, одними цифрами: '1' или None.

    `seps` — с разделителями разрядов и копеек: '1,100.00'. Слева и справа
    не прихватываем ни пикселя: там вплотную знак, и его край читался бы
    лишней цифрой.
    """
    x0, y0, x1, y1 = box
    m = max(3, (y1 - y0) // 4)
    crop = gray.crop((x0, max(0, y0 - m), x1, min(gray.height, y1 + m)))
    crop = ImageOps.expand(crop, border=12, fill=0 if _is_dark(crop) else 255)
    allowed = "0123456789" + (".,'" if seps else "")
    try:
        got = pytesseract.image_to_string(
            crop, config=f"--oem 3 --psm 7 -c tessedit_char_whitelist={allowed}")
    except Exception:
        return None
    got = got.strip()
    return got if re.fullmatch(r"\d[\d.,']*" if seps else r"\d+", got) else None


def _read_cents(gray, x_from, box):
    """Прочитать висящие копейки отдельно, одними цифрами. '99' или None."""
    x0, y0, x1, y1 = box
    # за край снимка не выходим: PIL добивает его чёрным, и полоса читалась бы цифрой
    crop = gray.crop((max(0, x_from - 2), y0, min(gray.width, x1 + 2),
                      y0 + int((y1 - y0) * 0.75)))
    crop = ImageOps.expand(crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS),
                           border=12, fill=0 if _is_dark(crop) else 255)
    try:
        got = pytesseract.image_to_string(
            crop, config="--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789")
    except Exception:
        return None
    got = got.strip()
    return got if re.fullmatch(r"\d{1,2}", got) else None


# Слово уже похоже на цену: знак валюты вплотную к цифрам и копейки после точки
_PRICE_SHAPE = re.compile(r"[$€£¥₹₽]\d.*[.,]\d\d$|^\d.*[.,]\d\d[$€£¥₹₽]$")


def _reread(clean, box, langs):
    """Прочитать одно слово отдельно от строки (рамка — в пикселях `clean`)."""
    x0, y0, x1, y1 = box
    m = max(3, (y1 - y0) // 3)
    crop = clean.crop((max(0, x0 - 2), max(0, y0 - m), min(clean.width, x1 + 2),
                       min(clean.height, y1 + m)))
    crop = ImageOps.expand(crop, border=12, fill=0 if _is_dark(crop) else 255)
    try:
        got = pytesseract.image_to_string(crop, lang=langs, config="--oem 3 --psm 7")
    except Exception:
        return None
    # мелкую точку одну распознавание нередко видит двоеточием: «$29:99»
    got = re.sub(r"(?<=\d)[:;](?=\d\d$)", ".", got.strip())
    return got if got and " " not in got else None


# --------------------------------------------------------------------------------------
#  Редкие знаки валют: сверка с образцами
# --------------------------------------------------------------------------------------
# Этих знаков модели Tesseract не знают вовсе: в алфавите английской из знаков
# валют только «€ £ ¥ $ ¢», в русской, украинской, казахской и турецкой их нет
# тоже, так что докачивать языки бесполезно. Незнакомый знак распознавание
# заменяет похожим: «100 ₴» -> «1002», «฿100» -> «6100», «₱100» -> «F100»,
# «100 ₼» -> «100 m», а «₹» и «₪» теряет совсем. Поэтому знак рядом с числом
# узнаём сами: вырезаем его с картинки и сравниваем с образцами, нарисованными
# шрифтами Windows. Среди образцов не только знаки, но и цифры, буквы и
# значки: знак берём, только если он заметно ближе любого из них.
RARE_SIGNS = "₴₹₪₱₫₾֏₼₮₿₩₺₸฿"
_SIGN_RIVALS = ("0123456789$€£¥₽¢%#@&§©®+*/()?!<>~=\"'"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                "БГДЖЗИЙЛПФЦЧШЩЪЫЬЭЮЯбвгдёжзийклмнптфцчшщъыьэюя"
                # значки, тайские и арабские буквы, иероглифы: без них «★» у
                # рейтинга сходил за «₩», а тайская «บาท» — за «֏»
                "★☆•·™°±×÷«»“”„…—–|\\^_{}[]"
                "กขคงจฉชซดตถทธนบปผพฟมยรลวสหอ"
                "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
                "（）价格元円包邮税込み")
# Шрифты, которые есть в любой Windows: ими набраны страницы и ими же браузер
# дорисовывает знаки, которых в шрифте сайта нет (Segoe UI Symbol, Nirmala UI —
# «₹», Leelawadee UI — «฿», Malgun Gothic — «₩», Sylfaen — «֏»).
_SIGN_FONTS = ["segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "arial.ttf", "arialbd.ttf",
               "calibri.ttf", "calibrib.ttf", "tahoma.ttf", "tahomabd.ttf", "times.ttf",
               "georgia.ttf", "cambria.ttc", "micross.ttf", "bahnschrift.ttf",
               "Nirmala.ttf", "LeelawUI.ttf", "malgun.ttf", "sylfaen.ttf", "msyh.ttc"]
_TILE = 16                  # сторона образца после приведения к одному размеру
# Вес отличий в расположении: высота знака, пропорции, где верх и низ —
# всё в долях высоты цифры. «₫» в Arial — маленький и приподнятый, «d» — нет.
_GEO_WEIGHTS = (0.175, 0.075, 0.125, 0.125)
_SIGN_SIZES = (26, 40)        # размеры, которыми рисуются образцы, px
_SIGN_MARGIN = 0.02         # насколько знак должен быть ближе лучшей не-знаковой догадки
_SIGN_TIE = 0.005           # ...и насколько ближе второго по сходству редкого знака
# Знаки, которые распознавание подставляет вместо редких: «₱» -> «P», «₸» -> «T»,
# «₿» и «฿» -> «B», «₩» -> «¥» или «#». Такой «знак» у числа всё равно сверяем:
# вдруг это «45₱», а не «45 ₽».
# Бывает, что и настоящим знаком: «₫1 299» -> «$1 299», «₺1 299» -> «£1 299».
_UNSURE_MARKS = {"P", "Р", "T", "Т", "B", "R", "#", "%", "@", "E", "©", "X", "<", "~",
                 "¥", "$", "£"}
_signs = None
_signs_lock = threading.Lock()
# Файл, где образцы хранятся между запусками: рисовать их — полторы секунды,
# читать готовые — сотые доли. Путь задаёт основной файл (рядом с курсами);
# None — рисовать каждый раз.
SIGNS_CACHE = None


def _ink_box(gray, threshold=140):
    """Рамка чернил на светлом фоне: (x0, y0, x1, y1) или None."""
    return gray.point(lambda v: 255 if v < threshold else 0).getbbox()


def _glyph_features(gray, box, dtop, dbottom):
    """Знак -> (картинка _TILE×_TILE, чернила светлые; геометрия относительно цифр)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    dh = max(1, dbottom - dtop)
    crop = ImageOps.autocontrast(ImageOps.invert(gray.crop(box)))
    side = max(w, h)
    sq = Image.new("L", (side, side), 0)
    sq.paste(crop, ((side - w) // 2, (side - h) // 2))
    tile = sq.resize((_TILE, _TILE), Image.BOX)
    geo = (h / dh, math.log(max(w, 1) / max(h, 1)), (y0 - dtop) / dh, (y1 - dbottom) / dh)
    return tile, geo


def _build_sign_templates():
    chars, geos, tiles = [], [], []
    for fname in _SIGN_FONTS:
        path = os.path.join(FONTS_DIR, fname)
        if not os.path.exists(path):
            continue
        for size in _SIGN_SIZES:
            try:
                font = ImageFont.truetype(path, size)
            except OSError:
                continue
            canvas = (size * 3, size * 2)

            def draw(ch):
                im = Image.new("L", canvas, 255)
                ImageDraw.Draw(im).text((size // 2, size // 3), ch, font=font, fill=0)
                return im

            notdef = draw("\U000F0000").tobytes()
            zero = draw("0")
            zbox = _ink_box(zero)
            if not zbox:
                continue
            for ch in RARE_SIGNS + _SIGN_RIVALS:
                im = draw(ch)
                box = _ink_box(im)
                if not box or im.tobytes() == notdef:
                    continue
                tile, geo = _glyph_features(im, box, zbox[1], zbox[3])
                chars.append(ch)
                geos.append(geo)
                tiles.append(tile)
    grid = math.ceil(math.sqrt(max(1, len(tiles))))
    sheet = Image.new("L", (grid * _TILE, grid * _TILE), 0)
    for k, tile in enumerate(tiles):
        r, c = divmod(k, grid)
        sheet.paste(tile, (c * _TILE, r * _TILE))
    return {"chars": chars, "geos": geos, "sheet": sheet, "grid": grid}


def _signs_key():
    """Отпечаток всего, от чего зависят образцы: сменились шрифты или настройки — рисуем заново."""
    parts = [str(_TILE), repr(_SIGN_SIZES), RARE_SIGNS, _SIGN_RIVALS]
    for fname in _SIGN_FONTS:
        path = os.path.join(FONTS_DIR, fname)
        parts.append(f"{fname}:{os.path.getsize(path) if os.path.exists(path) else 0}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _load_signs(path, key):
    try:
        with open(path, "rb") as f:
            head = json.loads(f.readline().decode("utf-8"))
            if head.get("key") != key:
                return None
            side = head["grid"] * _TILE
            sheet = Image.frombytes("L", (side, side), zlib.decompress(f.read()))
        return {"chars": list(head["chars"]), "geos": [tuple(g) for g in head["geos"]],
                "sheet": sheet, "grid": head["grid"]}
    except (OSError, ValueError, KeyError, zlib.error):
        return None


def _save_signs(path, key, t):
    head = {"key": key, "grid": t["grid"], "chars": "".join(t["chars"]),
            "geos": [[round(v, 5) for v in g] for g in t["geos"]]}
    try:
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(json.dumps(head, ensure_ascii=False).encode("utf-8") + b"\n")
            f.write(zlib.compress(t["sheet"].tobytes()))
        os.replace(tmp, path)
    except OSError:
        pass                        # не записалось — нарисуем в следующий раз


def _sign_templates():
    """Образцы: из файла, а если его нет или он устарел — рисуем (~1,5 с) и сохраняем."""
    global _signs
    with _signs_lock:
        if _signs is None:
            key = _signs_key()
            _signs = SIGNS_CACHE and _load_signs(SIGNS_CACHE, key)
            if not _signs:
                _signs = _build_sign_templates()
                if SIGNS_CACHE:
                    _save_signs(SIGNS_CACHE, key, _signs)
            _signs["rare"] = [ch in RARE_SIGNS for ch in _signs["chars"]]
        return _signs


def _tiled(tile, grid):
    """Клетка _TILE×_TILE, размноженная сеткой grid×grid."""
    row = Image.new("L", (grid * _TILE, _TILE))
    for c in range(grid):
        row.paste(tile, (c * _TILE, 0))
    sheet = Image.new("L", (grid * _TILE, grid * _TILE))
    for r in range(grid):
        sheet.paste(row, (0, r * _TILE))
    return sheet


def _distances(gray, box, dtop, dbottom, skip=None):
    """Знак на картинке -> [(расстояние, редкий ли, символ)] до каждого образца.

    Сравниваем разом со всеми образцами: образцы лежат одной сеткой, знак
    размножается такой же сеткой, и разница по клеткам считается одним
    уменьшением картинки — на две тысячи образцов уходит пара миллисекунд.

    `skip` — строки картинки (y0, y1), которые не сравниваем: там прошла линия
    зачёркивания, и от неё остались обрывки. Зачёркнутая «1» с ними похожа на
    «4»; без этих строк — снова на «1».
    """
    t = _sign_templates()
    tile, geo = _glyph_features(gray, box, dtop, dbottom)
    g = t["grid"]
    diff = ImageChops.difference(t["sheet"], _tiled(tile, g))
    gain = 1.0
    if skip:
        w, h = box[2] - box[0], box[3] - box[1]
        side = max(w, h)
        top = box[1] - (side - h) // 2              # где клетка начинается на картинке
        r0 = max(0, (skip[0] - top) * _TILE // side)
        r1 = min(_TILE, -(-(skip[1] + 1 - top) * _TILE // side))
        if 0 < r1 - r0 < _TILE:
            masks = t.setdefault("masks", {})
            if (r0, r1) not in masks:
                cell = Image.new("L", (_TILE, _TILE), 255)
                cell.paste(0, (0, r0, _TILE, r1))
                masks[(r0, r1)] = _tiled(cell, g)
            diff = ImageChops.multiply(diff, masks[(r0, r1)])
            gain = _TILE / (_TILE - (r1 - r0))      # строк меньше — разница на строку та же
    means = diff.resize((g, g), Image.BOX).getdata()
    w0, w1, w2, w3 = _GEO_WEIGHTS
    q0, q1, q2, q3 = geo
    return [(m / 255 * gain + w0 * abs(a0 - q0) + w1 * abs(a1 - q1) + w2 * abs(a2 - q2)
             + w3 * abs(a3 - q3), rare, ch)
            for m, rare, ch, (a0, a1, a2, a3) in zip(means, t["rare"], t["chars"], t["geos"])]


def _digit_guess(gray, box, dtop, dbottom, skip=None):
    """На какую цифру кусок похож больше всего — по тем же образцам."""
    return min((d, ch) for d, _, ch in _distances(gray, box, dtop, dbottom, skip)
               if ch.isdigit())[1]


def _classify_glyph(gray, box, dtop, dbottom):
    """Знак на картинке -> (лучший знак валюты, запас над лучшей другой догадкой)."""
    t = _sign_templates()
    if not t["chars"]:
        return None, 0.0
    best = {}
    best_rival = 9.0
    for d, rare, ch in _distances(gray, box, dtop, dbottom):
        if rare:
            if d < best.get(ch, 9.0):
                best[ch] = d
        elif d < best_rival:
            best_rival = d
    if not best:
        return None, 0.0
    ranked = sorted(best.items(), key=lambda kv: kv[1])
    sign, d = ranked[0]
    # Отрыв от второго редкого знака: «฿» и «₿» почти одинаковы, а спутать их —
    # пересчитать бат как биткоин. Ничью не решаем вовсе.
    runner = ranked[1][1] if len(ranked) > 1 else 9.0
    return (None if runner - d < _SIGN_TIE else sign), best_rival - d


def _far_from_digits(gray, box, dtop, dbottom, sign):
    """Знак, прочитанный цифрой и узнанный неуверенно, — точно ли не цифра.

    Шрифта сайта среди образцов может не быть: «₹» шрифтом Amazon похож на
    образцы «₹» лишь чуть больше, чем на «?» и «7», и запас выходит 0,00–0,02.
    Но распознавание прочло его цифрой («Upto ₹13.00» -> «Upto 213.00»), так
    что спор только между знаком и цифрой. Настоящие цифры того же шрифта ближе
    к образцам цифр на 0,08–0,18, знак — дальше от них на 0,05; берём от 0,03.
    """
    ds = _distances(gray, box, dtop, dbottom)
    digit = min(d for d, _, ch in ds if ch.isdigit())
    return digit - min(d for d, _, ch in ds if ch == sign) >= 0.03


def _blobs(gray, box, threshold=140):
    """Знаки в рамке по пустым столбцам: [(x0, y0, x1, y1), ...] слева направо."""
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(gray.width, x1), min(gray.height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return []
    ink = gray.crop((x0, y0, x1, y1)).point(lambda v: 255 if v < threshold else 0)
    cols = ink.resize((x1 - x0, 1), Image.BOX).getdata()
    out, start = [], None
    for x, v in enumerate(list(cols) + [0]):
        if v and start is None:
            start = x
        elif not v and start is not None:
            bb = ink.crop((start, 0, x, y1 - y0)).getbbox()
            if bb:
                out.append((x0 + start, y0 + bb[1], x0 + x, y0 + bb[3]))
            start = None
    return out


def _digit_band(blobs):
    """Верх и низ цифр по знакам числа: медиана по всем заметным знакам."""
    tall = max((b[3] - b[1] for b in blobs), default=0)
    big = [b for b in blobs if b[3] - b[1] >= 0.5 * tall]
    if not big:
        return None
    tops = sorted(b[1] for b in big)
    bottoms = sorted(b[3] for b in big)
    return tops[len(tops) // 2], bottoms[len(bottoms) // 2]


def _sure_mark(text, side):
    """Прочитанное рядом с числом — надёжный знак валюты (или множитель, «k», «млн»)?

    Не надёжен знак из _UNSURE_MARKS: его сверяем с образцами, даже если за ним
    что-то ещё — «₩» читается как «¥#».
    """
    s = text.translate(_NORM)
    if side == "post":
        found = _marker_after(s, 0) or _multiplier_after(s, 0)
        token = s[:found[1]] if found else None
    else:
        found = _marker_before(s, len(s))
        token = s[found[1]:] if found else None
    # русские двойники латинских букв сомнительны так же: с русской моделью
    # «₹29,990» читалось «Х29,990» с русской «Х», и знак не сверялся вовсе
    return bool(token) and token.strip().translate(_LATIN_TWIN) not in _UNSURE_MARKS


_SIGNIFICANT = re.compile(r"[^.,'\s]")


def _any_mark(text, side):
    """Прочитанное рядом с числом — хоть какой-то знак валюты (или множитель)?"""
    s = text.translate(_NORM)
    if side == "post":
        return bool(_marker_after(s, 0) or _multiplier_after(s, 0))
    return bool(_marker_before(s, len(s)))


def _union(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _edge_sign(clean, glyph, tall, side, dtop, dbottom, floor=_SIGN_MARGIN):
    """Крайний знак у числа -> (редкий знак или None, запас); `tall` — куски числа.

    Знак берём, если запас над лучшей другой догадкой не меньше `floor`.
    Спорно, если крайний кусок вместе с соседним тоже похож на знак: так бывает,
    когда знак распался надвое и половину распознавание прочло цифрой — «45₪»
    читалось «451m», и выходило 451 шекель. Неверная сумма хуже никакой.
    """
    dh = dbottom - dtop
    if glyph[3] - glyph[1] < 0.75 * dh and glyph[1] < dtop + 0.2 * dh \
            and glyph[3] < dbottom - 0.25 * dh:
        # Знак мелко сверху, как копейки: «₹132⁰⁰» на Amazon. В росте цифр он
        # для образцов слишком мал и высоко — сверяем в его собственном росте.
        dtop, dbottom = glyph[1], glyph[3]
    sign, margin = _classify_glyph(clean, glyph, dtop, dbottom)
    if not sign or margin < floor:
        return None, margin
    # Следующий кусок внутрь — среди тех, что со знаком не пересекаются: рамка
    # слова у Tesseract бывает шире и накрывает сам знак. Половинки одного
    # знака стоят вплотную; через пробел — это уже соседи.
    if side == "pre":
        inner = next((g for g in tall if g[0] >= glyph[2]), None)
    else:
        inner = next((g for g in reversed(tall) if g[2] <= glyph[0]), None)
    if inner and max(glyph[0] - inner[2], inner[0] - glyph[2]) < 0.2 * dh:
        # похоже на знак — даже если неясно, на какой из двух
        _, both = _classify_glyph(clean, _union(glyph, inner), dtop, dbottom)
        if both >= _SIGN_MARGIN:
            return None, margin
    return sign, margin


def _full_height(pieces, dtop, dbottom):
    """Куски в рост цифры: без точек, запятых и мелких копеек сверху.

    Запятая в Georgia и Trebuchet вдвое выше обычной и сходила за цифру:
    «₹3,999» выходило «₹33,999». Отличаем по верху — он у неё ниже середины.
    """
    dh = dbottom - dtop
    return [g for g in pieces if g[3] - g[1] >= 0.5 * dh and g[3] >= dbottom - 0.3 * dh
            and g[1] < dtop + 0.4 * dh]


def _recheck_digits(clean, text, pieces, side, dtop, dbottom):
    """Цифры числа, у которого узнан знак, — перепроверить без знака: текст или None.

    Незнакомый знак вплотную сбивает распознавание и соседней цифры: в Arial
    «₹132» читалось «<432», «(₹1,100.00» — «(4,100.00». Цифру у знака сверяем
    с образцами цифр (сотая доля секунды); сошлось — верим. Нет — перечитываем
    цифры в рост (без мелких копеек сверху) одними цифрами: четверть секунды,
    поэтому только тогда. Цифр столько же — берём новые на те же места; иначе
    None: неверная сумма хуже никакой. `pieces` — куски числа без знака.

    За числом в том же слове бывают буквы — «(₹440/kg)» читается слитно. Их
    куски тоже в рост, но к цифрам не относятся: после знака берём столько
    кусков, сколько цифр в числе сразу за ним.
    """
    full = _full_height(pieces, dtop, dbottom)
    places = [i for i, ch in enumerate(text) if ch.isdigit()]
    if side == "pre":
        run = re.match(r"\D*\d[\d.,']*", text)
        places = [i for i in places if i < run.end()] if run else []
        full = full[:len(places)]
    places = places[:len(full)]
    if not full or len(places) != len(full):
        return None
    k = 0 if side == "pre" else -1
    if _digit_guess(clean, full[k], dtop, dbottom) == text[places[k]]:
        return text
    got = _read_digits(clean, (full[0][0], min(g[1] for g in full),
                               full[-1][2], max(g[3] for g in full)), seps=True)
    got = re.sub(r"\D", "", got or "")
    if len(got) != len(full):
        return None
    chars = list(text)
    for i, d in zip(places, got):
        chars[i] = d
    return "".join(chars)


def _raised_run(clean, glyph, dtop, dbottom):
    """Цена с мелким знаком сверху, как на Amazon: «₹440⁰⁰».

    `glyph` — первый кусок слова. Если он мелкий и висит сверху, идём от него
    вправо по картинке, а не по рамке слова — число распознавание порой режет
    («₹418⁰⁰» -> «%4.1» и «8°»): сначала цифры в рост, потом мелкие копейки
    сверху. Возвращаем (куски цифр, куски копеек, сколько между цифрами точек и
    запятых) или None, если это не такая цена.
    """
    dh = dbottom - dtop
    # мельче трети цифры — не знак, а обломок буквы: засечка «T» в Georgia
    if not (0.3 * dh <= glyph[3] - glyph[1] < 0.75 * dh and glyph[1] < dtop + 0.2 * dh
            and glyph[3] < dbottom - 0.25 * dh):
        return None
    strip = _blobs(clean, (glyph[2], dtop - dh // 4, glyph[2] + 14 * dh, dbottom + dh // 4))
    run, tail, seps, prev = [], [], 0, glyph
    for g in strip:
        if g[0] - prev[2] > 0.45 * dh:
            break
        h = g[3] - g[1]
        if not tail and h >= 0.8 * dh and abs(g[1] - dtop) <= 0.12 * dh \
                and abs(g[3] - dbottom) <= 0.12 * dh:
            run.append(g)
        elif not tail and run and h <= 0.35 * dh and g[1] >= dbottom - 0.3 * dh:
            seps += 1                               # точка или запятая между разрядами
        elif run and 0.3 * dh <= h < 0.75 * dh and g[1] < dtop + 0.2 * dh \
                and g[3] < dbottom - 0.25 * dh:
            tail.append(g)
        else:
            break
        prev = g
    return (run, tail, seps) if run else None


def _number_at_sign(clean, run, tail, seps, dtop, dbottom, langs):
    """Число цены с мелким знаком сверху — читаем заново, отдельно от знака.

    Мелкий знак сбивает распознавание всего слова: «₹440» шрифтом Amazon
    читалось «2AAO», «AAD», «FAA» — цифры стали буквами. Те же цифры, вырезанные
    без знака, читаются верно. Берём, только если вышли одни цифры, их столько
    же, сколько кусков в рост на картинке, а точек и запятых не больше, чем
    нарисовано: неверная сумма хуже никакой. Копейки мелко сверху дочитываем
    отдельно. Четверть секунды, поэтому только когда прочитанное с картинкой
    не сходится.
    """
    got = _reread(clean, (run[0][0], min(g[1] for g in run),
                          run[-1][2], max(g[3] for g in run)), langs)
    got = (got or "").rstrip(".,'")
    if not re.fullmatch(r"\d[\d.,']*", got) or sum(ch.isdigit() for ch in got) != len(run) \
            or len(re.findall(r"[.,']", got)) > seps:
        return None
    if tail:
        cents = _read_cents(clean, tail[0][0], (tail[0][0], dtop, tail[-1][2], dbottom))
        if cents:
            got = f"{got}.{cents.ljust(2, '0')}"
    return got


def _own_blobs(clean, row, k):
    """Куски слова — без крайних, которые на деле соседнее слово.

    Рамка слова у Tesseract бывает шире самого слова: у «(₹440.00 / kg)» рамка
    числа накрывала косую черту, хотя та прочитана отдельным словом. Лишний
    кусок сбивал счёт знаков, и «₹», прочитанный цифрой «2», не правился.
    Снимаем только знаки препинания: короткое слово у числа может быть знаком
    валюты («100 ₴» читается «100 @»), а его кусок собирается из обеих рамок.
    """
    blobs = _blobs(clean, row[k][1])
    for near, end in ((k + 1, -1), (k - 1, 0)):
        if not 0 <= near < len(row):
            continue
        nwd, nb = row[near]
        if not re.fullmatch(r"[/|\\:;,.()\[\]{}-]+", nwd["text"].translate(_NORM)):
            continue
        while len(blobs) > 1:
            g = blobs[end]
            if min(g[2], nb[2]) - max(g[0], nb[0]) < 0.6 * (g[2] - g[0]):
                break
            blobs.pop(end)
    return blobs


def _dollar_or_s(rows, clean):
    """«$» и «S» распознавание путает: «R$100» читалось «RS100» — рупии вместо
    реалов, «SG$100» — «$G$100», доллары США. Различаем по картинке: черта «$»
    выходит выше и ниже цифр, а «S» ростом с них. Правим, только когда куски
    картинки и буквы слова сопоставляются один к одному.
    """
    for row in rows.values():
        for wd, b in row:
            text = wd["text"]
            if not re.search(r"\d", text) or not re.search(r"[S$]", text):
                continue
            blobs = _blobs(clean, b)
            band = _digit_band(blobs)
            if len(blobs) != len(text) or not band:
                continue
            dtop, dbottom = band
            dh = max(1, dbottom - dtop)
            chars = list(text)
            for i, (ch, g) in enumerate(zip(text, blobs)):
                sticks_out = g[1] <= dtop - 0.1 * dh and g[3] >= dbottom + 0.1 * dh
                flush = abs(g[1] - dtop) <= 0.05 * dh and abs(g[3] - dbottom) <= 0.05 * dh
                if ch == "S" and sticks_out:
                    chars[i] = "$"
                elif ch == "$" and flush and text[i + 1:i + 2].isalpha():
                    chars[i] = "S"                  # «$G$100»: первый — буква
            wd["text"] = "".join(chars)


def _find_rare_signs(rows, clean, scale, langs="eng"):
    """Узнаём редкие знаки у чисел и вписываем их в слова строки.

    Смотрим по обе стороны каждого числа, у которого с этой стороны знака нет:
      - соседнее короткое слово, которое знаком не прочиталось: «100 m» -> «100 ₼»;
      - лишний знак в начале или конце самого слова: «F100» -> «₱100»,
        «1002» -> «100₴» (знак прочитан цифрой — сверка это покажет);
      - чернила рядом, которые распознавание пропустило совсем: «₹100» -> «100».
    Сомнительный знак («$», «P», «¥» — за них распознавание принимает редкие)
    тоже сверяем: «₫1 299» читалось как «$1 299», и выходили доллары.
    Отдельно — цена с мелким знаком сверху («₹440⁰⁰» на Amazon): такой знак
    сбивает и цифры за ним, так что прочитанное сверяем с картинкой.
    """
    budget = 40                     # сверок на снимок (по ~10 мс) — хватит и на прайс
    rereads = 4                     # перечитываний числа отдельно от знака (по ~0,25 с)
    sure = set()                    # знаки, узнанные на снимке уверенно
    weak = []                       # (слово, было, станет, знак) — если знак есть в sure
    for key, row in rows.items():
        row.sort(key=lambda wb: wb[1][0])
        boxes = [b for _, b in row]
        additions = []
        for k, (wd, b) in enumerate(row):
            text = wd["text"]
            if budget <= 0:
                continue
            has_digit = re.search(r"\d", text)
            # Слов без цифр на странице сотни. Из них смотрим только короткие и
            # без строчных букв: так читается число, не узнанное цифрами.
            if not has_digit and (not 2 <= len(text) <= 10 or re.search(r"[a-zа-яё]", text)):
                continue
            blobs = _own_blobs(clean, row, k)
            band = _digit_band(blobs)
            if not band:
                continue
            dtop, dbottom = band
            dh = max(1, dbottom - dtop)
            # обрезок соседнего знака у левого края снимка — не начало слова:
            # выделяют часто впритык, и от «%» перед ценой остаётся полоска
            first = blobs[1] if len(blobs) > 2 and blobs[0][0] <= 0 else blobs[0]
            raised = _raised_run(clean, first, dtop, dbottom) if len(blobs) > 1 else None
            # цена занимает слово до конца: «₹99/мес» разбирается ниже, как раньше
            if raised and (raised[1] or raised[0])[-1][2] < blobs[-1][2] - 0.2 * dh:
                raised = None
            if raised:
                # Мелкий знак сверху, за ним цифры в рост. Прочитанному верим,
                # только если оно сходится с картинкой: цифр столько же, сколько
                # кусков, букв среди них нет, число не вышло за рамку слова,
                # первая цифра похожа на себя, а точек и запятых не больше, чем
                # нарисовано («₹440⁰⁰» читалось «44.0»). Иначе читаем число заново.
                run, tail, seps = raised
                edge = text[:has_digit.start()] if has_digit else ""
                body = text[len(edge):]
                digits = re.sub(r"\D", "", body)
                with_cents = bool(tail) and len(digits) == len(run) + len(tail)
                sign, margin = None, 0.0
                if not (edge and _sure_mark(edge, "pre")):      # «€19⁹⁹» — знак и так верный
                    budget -= 2
                    sign, margin = _edge_sign(clean, first, run, "pre", dtop, dbottom, 0.0)
                strong = sign and margin >= _SIGN_MARGIN
                mark = sign if strong else edge if edge and _any_mark(edge, "pre") else ""
                number = None
                if not (mark or sign):
                    pass        # не знак: в Georgia «1» и «2» мельче и выше соседних цифр
                elif run[-1][2] > b[2] + 0.2 * dh or re.search(r"[^\W\d_]", body) \
                        or len(digits) not in (len(run), len(run) + len(tail)) \
                        or len(re.findall(r"\d[.,'](?=\d)", body)) > seps + with_cents \
                        or _digit_guess(clean, run[0], dtop, dbottom) != digits[0]:
                    if rereads > 0:
                        rereads -= 1
                        number = _number_at_sign(clean, run, tail, seps, dtop, dbottom, langs)
                elif strong:
                    number = body
                if number and not mark:
                    # знак узнан неуверенно — число без него не трогаем, а правим,
                    # только если тот же знак найдётся на снимке уверенно
                    weak.append((wd, text, sign + number, sign))
                    number = None
                if number:
                    if strong:
                        sure.add(sign)
                    wd["text"] = mark + number
                    # куски той же цены, прочитанные отдельными словами, — в неё
                    end = (tail or run)[-1][2]
                    while k + 1 < len(row) and sum(row[k + 1][1][0::2]) / 2 < max(end, b[2]):
                        del row[k + 1]
                    if end > b[2]:
                        b = (b[0], min(b[1], dtop), end, max(b[3], dbottom))
                        row[k] = (wd, b)
                        wd["box"] = tuple(int(round(v / scale)) for v in b)
                    continue
            if not has_digit:
                continue
            # буква между цифрами («₿100» читалось «B1i00»): где тут число —
            # неясно, и знак у «1» дал бы один биткоин вместо ста
            if re.search(r"\d[^\d.,'\s]+\d", text):
                continue
            # Скобки по краям слова — «(₹1,100.00» — не знак и не цифра: снимаем
            # их вместе с их кусками. Иначе сверялась скобка, а до «₹»,
            # прочитанного цифрой «2», дело не доходило — выходило 21 100 рупий.
            lead = re.match(r"[(\[{]*", text).group()
            trail = re.search(r"[)\]}]*$", text).group()
            if lead or trail:
                ends = blobs[:len(lead)] + blobs[len(blobs) - len(trail):len(blobs)]
                if len(blobs) > len(lead) + len(trail) and all(
                        g[2] - g[0] <= 0.5 * dh and g[3] - g[1] >= 0.9 * dh for g in ends):
                    blobs = blobs[len(lead):len(blobs) - len(trail)]
                    wd["text"] = text[len(lead):len(text) - len(trail)]
                else:
                    # на картинке не скобка: «45₪» читалось «45m)» — это знак
                    lead = trail = ""
            # Скобка, которую распознавание не прочло вовсе: «(₹440» -> «2440».
            # Узкая и выше цифр в обе стороны — цифры и знаки валют такими не бывают.
            for end in (0, -1):
                g = blobs[end]
                if not (trail if end else lead) and len(blobs) > 2 and g[2] - g[0] <= 0.35 * dh \
                        and g[1] < dtop and g[3] >= dbottom + 0.1 * dh:
                    blobs = blobs[1:] if end == 0 else blobs[:-1]
            tall = [g for g in blobs if g[3] - g[1] >= 0.3 * dh]

            def around(side):
                """Что у числа с этой стороны: (край слова, близкое соседнее слово)."""
                text = wd["text"]
                digits = [i for i, ch in enumerate(text) if ch.isdigit()]
                edge = text[:digits[0]] if side == "pre" else text[digits[-1] + 1:]
                near = k - 1 if side == "pre" else k + 1
                if edge or not 0 <= near < len(row) or (lead if side == "pre" else trail):
                    return edge, None
                nwd, nb = row[near]
                gap = b[0] - nb[2] if side == "pre" else nb[0] - b[2]
                return edge, ((nwd, nb) if gap <= 1.0 * dh else None)

            for side in ("pre", "post"):
                if budget <= 0:
                    break
                other = "post" if side == "pre" else "pre"
                edge, neighbour = around(side)
                mark = edge or (neighbour[0]["text"] if neighbour else "")
                if mark and _sure_mark(mark, side):
                    continue
                # Знак у числа уже есть с другой стороны — здесь сверяем только
                # сомнительный знак, а новый не ищем: двух знаков у цены не бывает,
                # а иероглиф после «¥100» сходил за «₫».
                o_edge, o_neighbour = around(other)
                o_mark = o_edge or (o_neighbour[0]["text"] if o_neighbour else "")
                if o_mark and _any_mark(o_mark, other) and not (mark and _any_mark(mark, side)):
                    continue
                if neighbour:
                    # короткое соседнее слово — один знак, прочитанный буквой
                    nwd, nb = neighbour
                    nblobs = _blobs(clean, nb)
                    if len(nwd["text"]) <= 2 and len(nblobs) == 1:
                        # рамка короткого слова у Tesseract бывает уже самого
                        # знака, а рамка числа — шире и накрывает его край:
                        # собираем знак целиком из обеих
                        glyph = nblobs[0]
                        for g in tall:
                            if g[0] < glyph[2] and glyph[0] < g[2]:
                                glyph = _union(glyph, g)
                        budget -= 2
                        sign, _ = _edge_sign(clean, glyph, tall, side, dtop, dbottom)
                        if sign:
                            nwd["text"] = sign
                            sure.add(sign)
                        continue
                    if side == "pre" and len(nwd["text"]) <= 3 and 2 <= len(nblobs) <= 3 \
                            and not re.search(r"\d", nwd["text"]) and tall \
                            and all(g[3] - g[1] >= 0.8 * dh and abs(g[3] - dbottom) <= 0.15 * dh
                                    for g in nblobs[1:]):
                        # Знак и слипшиеся с ним цифры: «₹1» прочитано «=»,
                        # «₫1» — «di», «฿1» — «Bi». Цифры после знака дочитываем
                        # отдельно, одними цифрами; кусков должно выйти столько же.
                        rest = nblobs[1:]
                        budget -= 2
                        sign, _ = _edge_sign(clean, nblobs[0], rest + tall, side, dtop, dbottom)
                        got = sign and _read_digits(clean, (rest[0][0], min(g[1] for g in rest),
                                                            rest[-1][2], max(g[3] for g in rest)))
                        if got and len(got) == len(rest):
                            if tall[0][0] - rest[-1][2] <= 0.35 * dh:
                                # вплотную к числу — это его начало: «₹132», а не «₹1 32»
                                nwd["text"], wd["text"] = sign, got + wd["text"]
                            else:
                                nwd["text"] = sign + got         # «₫1 299»
                            sure.add(sign)
                            continue
                    # Сосед — обычное слово, а не знак: «Upto ₹13.00», «M.R.P.: ₹549».
                    # Знак тогда в самом числе, прочитан цифрой — «Upto 213.00».
                    if side == "post":
                        continue
                if len(edge) > 2:
                    continue
                # край самого слова: лишний знак или цифра, которой быть не должно
                if len(tall) >= 2:
                    glyph = tall[0] if side == "pre" else tall[-1]
                    budget -= 2
                    sign, margin = _edge_sign(clean, glyph, tall, side, dtop, dbottom,
                                              _SIGN_MARGIN if edge else 0.0)
                    if sign:
                        # Сколько знаков у числа без края — по картинке и по
                        # прочитанному. Не сходится — не трогаем: «45₪» читалось
                        # «451m», один знак двумя буквами, и замена «m» оставляла
                        # 451 шекель. Точки и запятые мелкие, их не считаем, а
                        # копейки мелко сверху («₹132⁰⁰») — считаем: висят над строкой.
                        text = wd["text"]
                        body = text[len(edge):] if side == "pre" else text[:len(text) - len(edge)]
                        # слипшиеся цифры («00» в Georgia) — один кусок, а знака два;
                        # жирная «4» в Tahoma шириной с рост цифры, пара — от полутора
                        drawn = sum(2 if g[2] - g[0] >= 1.3 * dh else 1
                                    for g in tall if g is not glyph and g[1] < dtop + 0.4 * dh
                                    and (g[3] - g[1] >= 0.5 * dh or g[3] < dbottom - 0.3 * dh))
                        read = len(_SIGNIFICANT.findall(body))
                        as_digit = not edge and drawn == read - 1
                        if edge and drawn == read:
                            text = sign + body if side == "pre" else body + sign
                        elif as_digit:                          # знак прочитан цифрой: «1002»
                            text = sign + text[1:] if side == "pre" else text[:-1] + sign
                        elif not edge and drawn == read and margin >= _SIGN_MARGIN:
                            text = sign + text if side == "pre" else text + sign   # не прочитан
                        else:
                            text = None
                        if text:
                            text = _recheck_digits(clean, text, [g for g in tall if g is not glyph],
                                                   side, dtop, dbottom)
                        if text and margin < _SIGN_MARGIN and not (
                                as_digit and _far_from_digits(clean, glyph, dtop, dbottom, sign)):
                            # Неуверенно, но цифры на этом месте тоже не видно:
                            # мелкая «(₹1,100.00» читалась «(21,100.00». Меняем,
                            # только если тот же знак нашёлся на снимке уверенно.
                            weak.append((wd, lead + wd["text"] + trail, lead + text + trail, sign))
                        elif text:
                            wd["text"] = text
                            sure.add(sign)
                            continue
                        if margin >= _SIGN_MARGIN:
                            continue
                if edge or (lead if side == "pre" else trail):
                    continue
                # чернила за рамкой слова, которые распознавание не взяло вовсе
                if side == "pre":
                    lo = max([nb[2] + 1 for nb in boxes if nb[2] <= b[0]] + [b[0] - int(1.3 * dh)])
                    area = (lo, dtop - dh // 2, b[0] - 1, dbottom + dh // 2)
                else:
                    hi = min([nb[0] - 1 for nb in boxes if nb[0] >= b[2]] + [b[2] + int(1.3 * dh)])
                    area = (b[2] + 1, dtop - dh // 2, hi, dbottom + dh // 2)
                found = [g for g in _blobs(clean, area) if g[3] - g[1] >= 0.3 * dh]
                if not found:
                    continue
                glyph = found[-1] if side == "pre" else found[0]
                gap = b[0] - glyph[2] if side == "pre" else glyph[0] - b[2]
                if gap > 1.0 * dh:
                    continue
                budget -= 2
                sign, _ = _edge_sign(clean, glyph, tall, side, dtop, dbottom)
                if not sign:
                    continue
                sure.add(sign)
                if gap <= 0.35 * dh:
                    # вплотную к цифрам — часть того же слова: «₹100», а не «₹ 100»;
                    # от этого зависит, чей знак, когда чисел в строке два
                    wd["text"] = sign + wd["text"] if side == "pre" else wd["text"] + sign
                    wd["box"] = tuple(int(round(v / scale)) for v in (
                        min(b[0], glyph[0]), b[1], max(b[2], glyph[2]), b[3]))
                else:
                    additions.append(({"text": sign, "conf": 0, "strike": wd["strike"],
                                       "box": tuple(int(round(v / scale)) for v in glyph)},
                                      glyph))
            wd["text"] = lead + wd["text"] + trail
        row.extend(additions)
        row.sort(key=lambda wb: wb[1][0])
    # Цифр, похожих на знак, среди тысяч проверенных не нашлось ни одной даже
    # с таким запасом, так что рядом с тем же знаком неуверенному верим
    for wd, was, now, sign in weak:
        if sign in sure and wd["text"] == was:
            wd["text"] = now
    return sure


# Знак из окружения берём, если кусок похож на него не меньше, чем на ближайшую
# цифру, на то, чем он прочитан, и на лучший из редких знаков, — с такими
# допусками. И с какого расстояния до образцов цифр кусок — уже не цифра: у
# зачёркнутых цифр оно 0,03–0,11, у знаков от 0,16.
_STRUCK_NEAR = (0.01, 0.03, 0.035)
_STRUCK_NOT_DIGIT = 0.15
# Какие прочитанные цифры правим по образцам: линия дорисовывает «1» перекладину,
# и выходит «4», а вместе с линией стирается середина «3», и выходит «2». В
# остальных расхождениях не прав бывает и тот, и другой: шрифтами Windows
# ошибаются образцы («7» -> «5»), мелким шрифтом Amazon — распознавание
# («9» -> «8», «0», «2», перевес образцов от 0,036). Поэтому такую цену не
# показываем вовсе — если только перевес образцов не мал: «9» и «0» без середины
# они сами путают, с перевесом до 0,02.
_STRUCK_SWAPS = {("4", "1"), ("2", "3")}
_STRUCK_SWAP_MARGIN = 0.015
_STRUCK_DOUBT_MARGIN = 0.03


def _check_struck(rows, clean, strikes, sure):
    """Зачёркнутые цены: знак валюты берём по окружению, цифры сверяем с образцами.

    Линия зачёркивания портит и знак, и цифры. Знак после стирания линии на
    себя почти не похож: «₹» шрифтом Amazon читается «3», «2», «$». Но на цифру
    он не похож тоже, а какая здесь валюта, видно рядом — тот же знак узнан у
    соседней цены или перед числом стоит «M.R.P.» (так старую цену подписывают
    в Индии). Цифры: «1» с обрывками линии читается как «4», и «₹1,299»
    выходило «₹4,299». Цифры сверяем с образцами, пропуская строки, где прошла
    линия. Замеры — на стенде зачёркнутых цен (семь шрифтов, три масштаба):
    где прочитанное и образцы совпали, ошибок не было ни одной. Где разошлись
    всерьёз — слово помечаем `doubt`, и find_prices такую цену не берёт.
    """
    budget = 12                     # зачёркнутых слов на снимок, по ~20 мс
    for row in rows.values():
        row.sort(key=lambda wb: wb[1][0])
        for k, (wd, b) in enumerate(row):
            text = wd["text"]
            if budget <= 0 or not wd["strike"] or not re.search(r"\d", text):
                continue
            line = next((s for s in strikes if b[1] < s[2] and s[3] < b[3]
                         and min(s[1], b[2]) - max(s[0], b[0]) >= 0.6 * (b[2] - b[0])), None)
            blobs = _own_blobs(clean, row, k)
            band = _digit_band(blobs)
            if not line or not band:
                continue
            budget -= 1
            dtop, dbottom = band
            dh = dbottom - dtop
            skip = (line[2] - 1, line[3] + 1)       # линия и по строке каймы с каждой стороны
            # Куски в рост и буквы слова — один к одному. Слипшаяся пара цифр
            # («00» в конце) — один кусок на две буквы; его не сверяем.
            pieces = []
            for g in _full_height(blobs, dtop, dbottom):
                pieces += [g] if g[2] - g[0] < 1.3 * dh else [None, None]
            places = [i for i, ch in enumerate(text) if _SIGNIFICANT.match(ch)]
            # обрывок линии за последней цифрой читается знаком препинания: «3549:»
            while len(places) > len(pieces) and text[places[-1]] in ":;-–—_~":
                places.pop()
            unread = len(pieces) == len(places) + 1     # первый кусок не прочитан вовсе
            if not pieces or not pieces[0] or not (unread or len(pieces) == len(places)):
                continue
            first = "" if unread else text[places[0]]
            expected = set(sure)
            if k and re.sub(r"[^A-Za-z]", "", row[k - 1][0]["text"]).upper() == "MRP":
                expected.add("₹")
            tail = text[max(i for i, ch in enumerate(text) if ch.isdigit()) + 1:]
            after = tail or (row[k + 1][0]["text"] if k + 1 < len(row) else "")
            if expected and not _any_mark(after, "post") and (
                    unread or first.isdigit() or first in "€£¥"
                    or first.translate(_LATIN_TWIN) in _UNSURE_MARKS):
                ds = _distances(clean, pieces[0], dtop, dbottom, skip)
                digit, guess = min((d, ch) for d, _, ch in ds if ch.isdigit())
                claim = min((d for d, _, ch in ds if ch == first), default=9.0)
                rare = min(d for d, is_rare, _ in ds if is_rare)
                near, sign = min((min((d for d, _, ch in ds if ch == s), default=9.0), s)
                                 for s in expected)
                if guess != first and digit >= _STRUCK_NOT_DIGIT \
                        and near <= min(digit + _STRUCK_NEAR[0], claim + _STRUCK_NEAR[1],
                                        rare + _STRUCK_NEAR[2]):
                    if unread:
                        text = sign + text
                        places = [i + 1 for i in places]
                    else:
                        text = text[:places[0]] + sign + text[places[0] + 1:]
                        places = places[1:]
                    pieces = pieces[1:]
                    unread = False
            if unread:
                continue
            chars = list(text)
            for i, g in zip(places, pieces):
                if not g or not chars[i].isdigit():
                    continue
                best = {}
                for d, _, ch in _distances(clean, g, dtop, dbottom, skip):
                    if ch.isdigit() and d < best.get(ch, 9.0):
                        best[ch] = d
                guess = min(best, key=best.get)
                lead = best[chars[i]] - best[guess]
                if (chars[i], guess) in _STRUCK_SWAPS and lead >= _STRUCK_SWAP_MARGIN:
                    chars[i] = guess
                elif lead >= _STRUCK_DOUBT_MARGIN:
                    wd["doubt"] = True
            wd["text"] = "".join(chars)


def read_lines(img, langs="eng", scale=2.0):
    """Распознать снимок -> строки слов: [[{text, box, conf, strike}, ...], ...].

    Разметка «psm 6» — «один сплошной кусок текста»: на странице магазина она
    держит цены лучше, чем разбор макета. На снимке кресла с зачёркнутой старой
    ценой разбор макета читал «8s. 29.990-66», а «psm 6» — «Rs. 29,990.00».
    Однострочной полоске лучше «psm 7».
    """
    scale = scale if img.height * scale < 4000 else 1.0
    # BICUBIC, а не LANCZOS: на снимках страниц из Chrome при масштабах экрана
    # 100–150 % с ним ошибок распознавания заметно меньше
    big = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                     Image.BICUBIC) if scale != 1.0 else img
    gray = _strip_frame(ImageOps.autocontrast(big.convert("L")))
    if _is_dark(gray):
        # светлые буквы на тёмном — переворачиваем: распознавание читает
        # тёмное по светлому, а белую цену на чёрной плашке порой не видит вовсе
        gray = ImageOps.invert(gray)
    gray = _boost_faint(ImageOps.autocontrast(gray))
    # линия зачёркивания — от 20 px в длину и не толще 3 px на исходном снимке
    clean, strikes = erase_strikes(gray, int(20 * scale), max(2, int(round(3 * scale))))
    psm = 7 if img.height < 40 else 6
    data = pytesseract.image_to_data(clean, lang=langs, config=f"--oem 3 --psm {psm}",
                                     output_type=Output.DICT)
    rows = {}
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not word or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        box = (x, y, x + w, y + h)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        row = rows.setdefault(key, [])
        # «$19⁹⁹» распознавание порой режет на два слова — «$1» и «9°9»: у
        # жирной «1» справа широкий просвет. Цифра вплотную к цифре, а в конце
        # висят копейки — это одно слово, и цена одна: $19.99, а не $1.
        if row and row[-1][0]["text"][-1:].isdigit() and word[0].isdigit():
            prev, pbox = row[-1]
            joined = (pbox[0], min(pbox[1], y), x + w, max(pbox[3], y + h))
            if 0 <= x - pbox[2] <= 0.3 * (joined[3] - joined[1]) \
                    and _raised_tail(clean, joined) is not None:
                row.pop()
                word, box, conf = prev["text"] + word, joined, min(conf, prev["conf"])
                x, y, w, h = joined[0], joined[1], joined[2] - joined[0], joined[3] - joined[1]
        # зачёркнута, если линия прошла через среднюю часть слова почти во всю его ширину
        strike = any(min(x + w, sx1) - max(x, sx0) >= 0.6 * w
                     and y + 0.25 * h <= sy0 <= y + 0.8 * h
                     for sx0, sx1, sy0, _ in strikes)
        if re.search(r"\d", word) and not re.search(r"\d[.,]\d", word):
            tail_x = _raised_tail(clean, box)
            if tail_x is not None:
                cents = _read_cents(clean, tail_x, box)
                if cents:
                    head = re.match(r"^(\D*)(\d+)", word)
                    if head:
                        digits = head.group(2)
                        # «19€⁹⁹» — Франция: знак стоит на месте запятой, между целыми
                        # и копейками. Знак не теряем, а копеек в цифрах перед ним
                        # нет: «299€⁹⁹» — это 299,99, а не «2.99»
                        sign = _marker_after(word[head.end():].translate(_NORM), 0)
                        sign = sign[0] if sign and not sign[0][0].isalnum() else ""
                        if not sign and digits.endswith(cents) and len(digits) > len(cents):
                            digits = digits[:-len(cents)]
                        word = f"{head.group(1)}{digits}.{cents.ljust(2, '0')}{sign}"
        row.append(({"text": word, "conf": conf, "strike": strike,
                     "box": tuple(int(round(v / scale)) for v in box)}, box))
    # Мелкая старая цена в одной строке с крупной новой: распознавание
    # подгоняет всю строку под крупную, мелкая ужимается, и точка из «$29.99»
    # пропадает — выходит «$2999», в сто раз дороже, а «$» под линией
    # становится «5»: «529.99». Такое слово перечитываем отдельно и берём
    # новое прочтение, только если оно похоже на цену, а цифры те же — или
    # те же без первой, которая и была знаком валюты. Каждое перечитывание —
    # ещё четверть секунды, поэтому их не больше трёх, зачёркнутые — первыми.
    small = []
    for ws in rows.values():
        tall = max((b[3] - b[1] for wd, b in ws if re.search(r"\d", wd["text"])), default=0)
        small += [(wd, b) for wd, b in ws
                  if len(re.sub(r"\D", "", wd["text"])) >= 3 and b[3] - b[1] < 0.75 * tall
                  and not _PRICE_SHAPE.search(wd["text"])]
    small.sort(key=lambda wb: not wb[0]["strike"])
    for wd, b in small[:3]:
        old, again = wd["text"], _reread(clean, b, langs)
        if not again or not _PRICE_SHAPE.search(again):
            continue
        was, now = re.sub(r"\D", "", old), re.sub(r"\D", "", again)
        if now == was or (now == was[1:] and old[0].isdigit() and not again[0].isdigit()):
            wd["text"] = again
    _dollar_or_s(rows, clean)
    sure = _find_rare_signs(rows, clean, scale, langs)
    _check_struck(rows, clean, strikes, sure)
    lines = [sorted((wd for wd, _ in ws), key=lambda w: w["box"][0]) for ws in rows.values()]
    lines.sort(key=lambda ws: (min(w["box"][1] for w in ws), ws[0]["box"][0]))
    return lines


# --------------------------------------------------------------------------------------
#  Курсы
# --------------------------------------------------------------------------------------
CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
# Запасной источник: 300 с лишним валют, включая криптовалюты, без ключа и без
# лимитов. Два адреса одного и того же — на случай, если один недоступен.
FALLBACK_URLS = [
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json",
    "https://latest.currency-api.pages.dev/v1/currencies/usd.min.json",
]
RATES_MAX_AGE = 6 * 3600       # ЦБ меняет курс раз в день; чаще спрашивать незачем


class RatesUnavailable(Exception):
    pass


class Rates:
    """Курсы как «рублей за единицу». Кросс-курс любых двух — через рубль.

    Рубль взят опорой потому, что основной источник — ЦБ РФ: его курс для
    человека в России привычный и официальный. Чего у ЦБ нет (тайваньский
    доллар, ринггит, криптовалюты), досчитывается из запасного источника через
    доллар ЦБ — так обе части сходятся в одной точке, а не живут порознь.
    """

    def __init__(self, rub, source, cbr_date="", fb_date="", fetched=0.0, stale=False):
        self.rub = rub                  # код -> рублей за 1 единицу
        self.source = source            # код -> "cbr" | "fb"
        self.cbr_date = cbr_date        # «26.09.2026»
        self.fb_date = fb_date          # «2026-09-25»
        self.fetched = fetched
        self.stale = stale              # свежие не скачались, это сохранённые

    def convert(self, amount, src, dst):
        if src == dst:
            return amount
        a, b = self.rub.get(src), self.rub.get(dst)
        if not a or not b:
            return None
        return amount * a / b

    def has(self, code):
        return bool(self.rub.get(code))

    def to_json(self):
        return {"rub": self.rub, "source": self.source, "cbr_date": self.cbr_date,
                "fb_date": self.fb_date, "fetched": self.fetched}


def _fetch_cbr(timeout):
    r = requests.get(CBR_URL, timeout=timeout)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rates = {}
    for v in root:
        code = (v.findtext("CharCode") or "").strip()
        try:
            unit = v.findtext("VunitRate")
            if unit:
                rate = float(unit.replace(",", "."))
            else:
                rate = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal"))
        except (TypeError, ValueError, AttributeError):
            continue
        if code and rate > 0:
            rates[code] = rate
    if "USD" not in rates:
        raise ValueError("в ответе ЦБ нет доллара")
    return rates, root.get("Date", "")


def _fetch_fallback(timeout):
    last = None
    for url in FALLBACK_URLS:
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            usd = {k.upper(): float(v) for k, v in (data.get("usd") or {}).items()
                   if isinstance(v, (int, float)) and v > 0}
            if "RUB" in usd:
                return usd, str(data.get("date", ""))
        except Exception as e:
            last = e
    raise last or ValueError("запасной источник курсов не ответил")


def _merge(cbr, cbr_date, usd, fb_date, fetched):
    rub, source = {"RUB": 1.0}, {"RUB": "cbr"}
    for code, rate in (cbr or {}).items():
        rub[code], source[code] = rate, "cbr"
    if usd:
        per_usd = rub.get("USD") or usd.get("RUB")
        for code, units in usd.items():
            if code not in rub and per_usd:
                rub[code], source[code] = per_usd / units, "fb"
    return Rates(rub, source, cbr_date if cbr else "", fb_date if usd else "", fetched)


_rates_lock = threading.Lock()


def load_rates(cache_path, log=lambda *a: None, timeout=6):
    """Курсы: свежий кэш — сразу, иначе качаем. Не скачалось — старый кэш.

    Качаем оба источника одновременно: ЦБ отвечает за 0.3 с, запасной — за 0.2,
    а последовательно это время складывалось бы. Не отозвался один — живём с
    другим; не отозвался никто и кэша нет — RatesUnavailable.
    """
    with _rates_lock:
        cached = None
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            cached = Rates(data["rub"], data.get("source", {}), data.get("cbr_date", ""),
                           data.get("fb_date", ""), float(data.get("fetched", 0)))
        except Exception:
            pass
        if cached and time.time() - cached.fetched < RATES_MAX_AGE:
            return cached

        got = {}

        def run(name, fn):
            try:
                got[name] = fn(timeout)
            except Exception as e:
                log(f"курсы: {name} не ответил: {e}")

        threads = [threading.Thread(target=run, args=("cbr", _fetch_cbr), daemon=True),
                   threading.Thread(target=run, args=("fb", _fetch_fallback), daemon=True)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout + 1)
        cbr, fb = got.get("cbr"), got.get("fb")
        if not cbr and not fb:
            if cached:
                cached.stale = True
                return cached
            raise RatesUnavailable()
        rates = _merge(cbr[0] if cbr else None, cbr[1] if cbr else "",
                       fb[0] if fb else None, fb[1] if fb else "", time.time())
        if cached and not cbr:
            # ЦБ сегодня молчит, а вчерашние его курсы есть — они точнее запасных
            for code, src in cached.source.items():
                if src == "cbr" and code in cached.rub:
                    rates.rub[code], rates.source[code] = cached.rub[code], "cbr"
            rates.cbr_date = cached.cbr_date
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(rates.to_json(), f, ensure_ascii=False, indent=1)
        except OSError:
            pass
        log(f"курсы обновлены: ЦБ {rates.cbr_date or '—'}, запасной {rates.fb_date or '—'}, "
            f"валют {len(rates.rub)}")
        return rates


# --------------------------------------------------------------------------------------
#  Запись сумм
# --------------------------------------------------------------------------------------
# NBSP внутри суммы: «8 795 ₽» не должно рваться на две строки
NBSP = "\u00a0"


def fmt_amount(value, code, comma=True, short=None):
    """8794.6, RUB -> «8 795 ₽»; 117.23, USD -> «$117»; 41.5, USD -> «$41,50».

    До целых округляем всё от сотни: копейки у суммы в тысячи рублей только
    мешают читать. Меньше сотни — с копейками, если у валюты они вообще есть
    (у иены и воны их нет). Совсем мелкие доли — у криптовалют — значащими цифрами.

    `short` — слова для тысяч, миллионов, миллиардов на языке интерфейса:
    («тыс.», «млн», «млрд»). Передают его для цен, записанных коротко.
    """
    sym, prefix, decimals = CURRENCIES.get(code, (code, False, 2))[:3]
    if short and abs(value) >= 1e6:
        # «$1.2M» -> «101,2 млн ₽», а не «101 209 680 ₽»: раз оригинал записан
        # коротко, точность до рубля там никому не нужна
        k, word = (1e9, short[2]) if abs(value) >= 1e9 else (1e6, short[1])
        v = value / k
        text = f"{v:,.{1 if v < 100 else 0}f}".replace(",", NBSP)
        text = text[:-2] if text.endswith(".0") else text
        if comma:
            text = text.replace(".", ",")
        text += ("" if len(word) == 1 else NBSP) + word
        return f"{sym}{text}" if prefix else f"{text}{NBSP}{sym}"
    places = 0 if abs(value) >= 100 else min(2, decimals)
    if abs(value) < 0.01 and value:
        text = f"{value:.2g}"
    else:
        text = f"{value:,.{places}f}".replace(",", NBSP)
        if comma:
            text = text.replace(".", ",")
    return f"{sym}{text}" if prefix else f"{text}{NBSP}{sym}"


def fmt_rate(value, code, comma=True):
    """Курс — цена одной единицы: 8.51342, RUB -> «8,5134 ₽»; 84.331 -> «84,33 ₽».

    Точнее, чем сумма: курс в копейки округлять нельзя — у иены он 0,58 ₽, у
    воны 0,06 ₽, и два знака съели бы его почти целиком. Поэтому до десятки —
    четыре знака после запятой, как у ЦБ, а совсем мелкий — четыре значащие
    цифры. От десятки хватает копеек, от тысячи — целых.
    """
    sym, prefix = CURRENCIES.get(code, (code, False))[:2]
    v = abs(value)
    if v >= 1000:
        places = 0
    elif v >= 10:
        places = 2
    elif v >= 0.01 or not v:
        places = 4
    else:
        places = 3 - math.floor(math.log10(v))
    text = f"{value:,.{places}f}".replace(",", NBSP)
    if comma:
        text = text.replace(".", ",")
    return f"{sym}{text}" if prefix else f"{text}{NBSP}{sym}"


# --------------------------------------------------------------------------------------
#  Отрисовка
# --------------------------------------------------------------------------------------
FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_FONT_FILES = {False: ["segoeui.ttf", "arial.ttf", "tahoma.ttf"],
               True: ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "tahomabd.ttf"]}
_fonts = {}


def _font(size, bold):
    key = (size, bold)
    if key not in _fonts:
        font = None
        for name in _FONT_FILES[bold]:
            path = os.path.join(FONTS_DIR, name)
            if os.path.isfile(path):
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except Exception:
                    continue
        _fonts[key] = font or ImageFont.load_default()
    return _fonts[key]


def _colors(region):
    """Фон и цвет букв в рамке цены. Фон — по краю рамки, буквы — самое непохожее на фон.

    Цвет букв берём у оригинала: красная цена распродажи должна остаться
    красной, иначе пересчёт выглядит как чужая надпись поверх страницы.
    """
    rgb = region.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    edge = [px[x, y] for x in range(w) for y in (0, h - 1)] + \
           [px[x, y] for y in range(h) for x in (0, w - 1)]
    votes = {}
    for c in edge:
        key = (c[0] // 16, c[1] // 16, c[2] // 16)
        votes[key] = votes.get(key, 0) + 1
    win = max(votes.items(), key=lambda kv: kv[1])[0]
    same = [c for c in edge if (c[0] // 16, c[1] // 16, c[2] // 16) == win]
    bg = tuple(sum(c[i] for c in same) // len(same) for i in range(3))
    near = sum(1 for c in edge if sum(abs(c[i] - bg[i]) for i in range(3)) <= 60)
    dist = [(sum(abs(c[i] - bg[i]) for i in range(3)), c)
            for c in rgb.getdata()]
    far = max(d for d, _ in dist) if dist else 0
    if far < 60:
        light = sum(bg) / 3 > 140
        return bg, ((20, 20, 20) if light else (240, 240, 240)), near >= 0.85 * len(edge), 0.0
    ink = [c for d, c in dist if d >= 0.6 * far]
    fg = tuple(sum(c[i] for c in ink) // len(ink) for i in range(3))
    share = len([d for d, _ in dist if d >= 0.5 * far]) / max(1, len(dist))
    return bg, fg, near >= 0.85 * len(edge), share


def _row_of(box, words):
    """Слова той же строки, что и рамка: по высоте перекрываются хотя бы на 40 %."""
    x0, y0, x1, y1 = box
    h = max(1, y1 - y0)
    return [w for w in words
            if min(w["box"][3], y1) - max(w["box"][1], y0) > 0.4 * min(h, w["box"][3] - w["box"][1])]


def render(img, prices, labels, lines, zoom=1.0, min_font=8):
    """Рисуем пересчёт поверх цен. `labels` — подпись к каждой цене («8 795 ₽ · $104»).

    Подпись почти всегда длиннее цены: «Rs. 9,999.00» против «8 795 ₽ · $104».
    Ужимать её шрифт до влезания нельзя — у «149,99 SGD (includes GST)» от
    суммы оставалось мелкое пятнышко. Поэтому подпись рисуется в размер
    оригинала, а всё, что правее в той же строке, сдвигается вправо ровно на
    нехватку — как если бы текст на странице набрали заново. Картинка от этого
    становится шире, и только.

    Цены одной строки идут слева направо: новая и зачёркнутая старая
    расставляются друг за другом, а не ложатся одна на другую.
    """
    zoom = max(1.0, float(zoom or 1.0))
    src = img.convert("RGB")
    if zoom > 1.001:
        src = src.resize((int(src.width * zoom), int(src.height * zoom)), Image.LANCZOS)
    out = src.copy()
    draw = ImageDraw.Draw(out)

    def z(box):
        return tuple(int(round(v * zoom)) for v in box)

    words = [dict(w, box=z(w["box"])) for line in lines for w in line]
    items = [(z(p.box), p, label) for p, label in zip(prices, labels) if label]
    # строки: цены, перекрывающиеся по высоте
    rows = []
    for item in sorted(items, key=lambda it: (it[0][1], it[0][0])):
        y0, y1 = item[0][1], item[0][3]
        for row in rows:
            if min(y1, row["y1"]) - max(y0, row["y0"]) > 0.4 * min(y1 - y0, row["y1"] - row["y0"]):
                row["items"].append(item)
                row["y0"], row["y1"] = min(row["y0"], y0), max(row["y1"], y1)
                break
        else:
            rows.append({"y0": y0, "y1": y1, "items": [item]})

    for row in rows:
        row["items"].sort(key=lambda it: it[0][0])
        shift = 0
        for n, (box, p, label) in enumerate(row["items"]):
            ox0, oy0, ox1, oy1 = box
            x0, y0, x1, y1 = ox0 + shift, oy0, ox1 + shift, oy1
            h = max(4, y1 - y0)
            # цвета — с исходного снимка: на `out` уже могли лечь заливки соседей
            region = src.crop((max(0, ox0 - 2), max(0, oy0 - 2),
                               min(src.width, ox1 + 2), min(src.height, oy1 + 2)))
            bg, fg, even, share = _colors(region)
            dx0, dx1 = (int(round(v * zoom)) for v in p.digits_x)
            top, digit_h = _digit_rows(src.crop((max(0, dx0), max(0, oy0 - 2),
                                                 min(src.width, max(dx0 + 2, dx1)),
                                                 min(src.height, oy1 + 2))), bg)
            if digit_h:
                y0 = max(0, oy0 - 2) + top
            else:
                digit_h = h
            bold = share > BOLD_SHARE
            size = max(min_font, int(round(digit_h / _digit_em(bold))))
            font = _font(size, bold)
            need = int(draw.textlength(label, font=font)) + 2
            gap = max(4, size // 3)

            # что стоит правее в этой строке: чужое слово, следующая цена или
            # хвост того же слова («$4.99/mo» — «/mo» надо сдвинуть, а не закрыть)
            same_row = _row_of(box, words)
            nxt = None
            for w in same_row:
                wx0, _, wx1, _ = w["box"]
                if wx0 >= ox1 - 1:
                    cand = wx0
                elif wx1 > ox1 + 2 and wx0 <= ox1:
                    cand = ox1
                else:
                    continue
                nxt = cand if nxt is None else min(nxt, cand)
            for later_box, _, _ in row["items"][n + 1:]:
                nxt = later_box[0] if nxt is None else min(nxt, later_box[0])
            limit = (nxt + shift - gap) if nxt is not None else out.width - 2
            lack = x0 + need - limit
            if lack > 0:
                band_y0 = max(0, min([y0] + [w["box"][1] for w in same_row]) - 2)
                band_y1 = min(out.height, max([y1] + [w["box"][3] for w in same_row]) + 2)
                out = _widen(out, lack)
                if nxt is not None:
                    cut = nxt + shift
                    piece = out.crop((cut, band_y0, out.width - lack, band_y1))
                    out.paste(piece, (cut + lack, band_y0))
                    out.paste(Image.new("RGB", (lack, band_y1 - band_y0), bg), (cut, band_y0))
                    shift += lack
                draw = ImageDraw.Draw(out)

            # заливка — по всей старой рамке со знаком валюты, а не только по
            # цифрам, и с запасом справа: точка от «руб.» выходит за рамку слова.
            # По высоте — по всем словам цены: рамка цены взята по цифрам, а
            # «руб.» и «грн» опускаются ниже них, и хвосты «р» и «у» торчали
            under = [w["box"] for w in same_row
                     if min(w["box"][2], ox1) - max(w["box"][0], ox0)
                     > 0.5 * min(w["box"][2] - w["box"][0], ox1 - ox0)]
            cover = (x0 - 1, min([oy0, y0] + [b[1] for b in under]) - 2,
                     max(x1 + max(2, size // 6), x0 + need) + 1,
                     max([oy1] + [b[3] for b in under]) + 2)
            cx0, cy0 = max(0, cover[0]), max(0, cover[1])
            cx1, cy1 = min(out.width, cover[2]), min(out.height, cover[3])
            if cx1 > cx0 and cy1 > cy0:
                patch = Image.new("RGB", (cx1 - cx0, cy1 - cy0), bg)
                if not even:
                    # под ценой картинка или градиент — ровная плашка там заплатка,
                    # размытие честнее
                    base = out.crop((cx0, cy0, cx1, cy1)).filter(
                        ImageFilter.GaussianBlur(max(2, (cy1 - cy0) // 5)))
                    patch = Image.blend(base, patch, 0.9)
                out.paste(patch, (cx0, cy0))
            # верх цифр новой суммы — там же, где был верх цифр старой
            ty = y0 - font.getbbox("0")[1]
            draw.text((x0, ty), label, font=font, fill=fg)
            if p.strike:
                _, top, _, bottom = draw.textbbox((x0, ty), "0", font=font)
                mid = top + (bottom - top) * 0.5
                draw.line((x0, mid, x0 + need - 2, mid), fill=fg,
                          width=max(1, size // 14))
    return out


def _digit_rows(region, bg):
    """Где в рамке цены сами цифры по высоте: (отступ сверху, высота) или (0, 0).

    По рамке Tesseract кегль выходит неточным: в рамку «$4.99» входят хвосты
    доллара, которые выше и ниже цифр, а у китайской модели рамки бывают выше
    знаков на треть. Поэтому считаем строки пикселей с чернилами: строки цифр
    заполнены плотно, а хвост «$» или запятая — это один тонкий штрих, и
    такие строки отбрасываются порогом.
    """
    rgb = region.convert("RGB")
    w, h = rgb.size
    if w < 2 or h < 4:
        return 0, 0
    px = rgb.load()
    dist = [[sum(abs(px[x, y][i] - bg[i]) for i in range(3)) for x in range(w)] for y in range(h)]
    far = max(max(row) for row in dist)
    if far < 60:
        return 0, 0
    counts = [sum(1 for d in row if d >= 0.5 * far) for row in dist]
    # сплошная строка во всю ширину — линия зачёркивания, а не цифры: по ней
    # порог выходил таким высоким, что от цифр не оставалось ничего
    body = [n for n in counts if n < 0.8 * w]
    peak = max(body) if body else 0
    if not peak:
        return 0, 0
    rows = [y for y, n in enumerate(counts) if 0.3 * peak <= n < 0.8 * w]
    return rows[0], rows[-1] - rows[0] + 1


_em = {}


def _digit_em(bold):
    """Высота цифры в долях кегля у шрифта подписи (у Segoe UI около 0.7)."""
    if bold not in _em:
        box = _font(100, bold).getbbox("0")
        _em[bold] = max(0.3, (box[3] - box[1]) / 100)
    return _em[bold]


def _widen(img, extra):
    """Картинка шире на `extra` пикселей справа, запас — цветом правого края."""
    col = img.crop((img.width - 1, 0, img.width, img.height)).convert("RGB")
    colors = col.getcolors(img.height + 1) or [(1, (255, 255, 255))]
    edge = max(colors)[1]
    wide = Image.new("RGB", (img.width + extra, img.height), edge)
    wide.paste(img, (0, 0))
    return wide


# Доля «чернил» в рамке цены, выше которой цена набрана жирным. Подобрано по
# снимкам: обычный текст даёт 0.12–0.22, жирная цена магазина — 0.26–0.30.
BOLD_SHARE = 0.24
