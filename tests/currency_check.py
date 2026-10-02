# -*- coding: utf-8 -*-
"""Проверочный набор для пересчёта цен (currency.py) — прогонять после каждой правки.

Каждый случай рисуется картинкой, как цена выглядит на сайте, и проходит тот же
путь, что в программе: распознавание -> разбор. Смотрим не на текст, а на
картинку, потому что главные поломки случаются раньше разбора — распознавание
видит «₽» как «P», «₹» как «%», а зачёркнутую «$29.99» как «52933».

Запуск:  python tests\\currency_check.py
В конце — число расхождений; 0 — всё как было. Нужен Tesseract с eng, rus и
chi_sim (как у программы из исходников). В интернет не ходит.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.argv = sys.argv[:1]

import screen_translator  # noqa: E402,F401  (находит tesseract.exe и языки)
import currency  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# (текст, стиль, валюта на экране, ожидаемое [(сумма, код, зачёркнута[, рамка])])
# стиль: "" — обычный, "big" — крупная жирная цена, "sup" — копейки мелко сверху
# после «|», "strike" — первая цена зачёркнута, "web:…" и "dark:…" — старая и
# новая цена через «|», нарисованные как в браузере (draw_web). Пустой список —
# «не трогать». Рамка — что закроет пересчёт, как это прочло распознавание
# («₽» оно читает как «P»); где её нет, рамку не сверяем.
CASES = [
    # разделители
    ("149,99 SGD (includes GST)", "", "auto", [(149.99, "SGD", False)]),
    ("$1,299.99", "big", "auto", [(1299.99, "USD", False)]),
    ("1.299,99 €", "big", "auto", [(1299.99, "EUR", False)]),
    ("1 299,99 ₽", "big", "auto", [(1299.99, "RUB", False)]),
    ("CHF 1'299.50", "", "auto", [(1299.5, "CHF", False)]),
    ("₹1,23,456.00", "", "auto", [(123456, "INR", False)]),
    ("Total: 1,234 $", "", "auto", [(1234, "USD", False)]),
    ("Rp 150.000", "", "auto", [(150000, "IDR", False)]),
    ("KWD 12.500", "", "auto", [(12.5, "KWD", False)]),
    # какой доллар
    ("$19.99", "", "auto", [(19.99, "USD", False)]),
    ("S$25.00", "", "auto", [(25, "SGD", False)]),
    ("A$49.95", "", "auto", [(49.95, "AUD", False)]),
    ("CA$30", "", "auto", [(30, "CAD", False)]),
    ("C$100", "", "auto", [(100, "CAD", False)]),
    ("HK$388", "", "auto", [(388, "HKD", False)]),
    ("US$99", "", "auto", [(99, "USD", False)]),
    ("R$ 59,90", "", "auto", [(59.9, "BRL", False)]),
    ("NT$1,290", "", "auto", [(1290, "TWD", False)]),
    ("MX$ 450", "", "auto", [(450, "MXN", False)]),
    ("$19.99", "", "SGD", [(19.99, "SGD", False)]),
    # похожие знаки
    ("¥1,280", "", "auto", [(1280, "JPY", False)]),
    ("￥99.00 包邮", "", "auto", [(99, "CNY", False)]),
    ("99元", "", "auto", [(99, "CNY", False)]),
    ("199 kr", "", "auto", [(199, "SEK", False)]),
    ("199 kr", "", "NOK", [(199, "NOK", False)]),
    ("RM 29.90", "", "auto", [(29.9, "MYR", False)]),
    ("Br 25,50", "", "auto", [(25.5, "BYN", False)]),
    ("49,90 zł", "", "auto", [(49.9, "PLN", False)]),
    ("1 290 Kč", "", "auto", [(1290, "CZK", False)]),
    ("AED 150", "", "auto", [(150, "AED", False)]),
    ("CHF 49.–", "", "auto", [(49, "CHF", False)]),
    # Редкие знаки: моделям распознавания они незнакомы, их узнаёт сверка с
    # образцами. Где сверка не уверена («₸» мелко похож на «T»), в «авто» не
    # трогаем, а с выбранной валютой пересчитываем.
    ("₩12,900", "", "auto", [(12900, "KRW", False)]),
    ("₩12,900", "", "KRW", [(12900, "KRW", False)]),
    ("100 ₴", "big", "auto", [(100, "UAH", False)]),
    ("Цена: 45₴", "big", "auto", [(45, "UAH", False)]),
    ("₹1,299", "", "auto", [(1299, "INR", False)]),
    ("45 ₪", "", "auto", [(45, "ILS", False)]),
    ("₱100", "", "auto", [(100, "PHP", False)]),
    ("Total ₱ 1,250", "", "auto", [(1250, "PHP", False)]),
    ("250.000 ₫", "", "auto", [(250000, "VND", False)]),
    ("100 ₾", "", "auto", [(100, "GEL", False)]),
    ("5 000 ֏", "", "auto", [(5000, "AMD", False)]),
    ("100 ₼", "", "auto", [(100, "AZN", False)]),
    ("100 ₮", "", "auto", [(100, "MNT", False)]),
    ("₿0.5", "", "auto", [(0.5, "BTC", False)]),
    ("₺349,90", "", "auto", [(349.9, "TRY", False)]),
    # а похожие на них буквы у чисел — не знаки
    ("5 W", "", "auto", []),
    ("100 T-shirts", "", "auto", []),
    ("B100", "big", "auto", []),
    ("12 m", "big", "auto", []),
    ("Order 1002", "", "auto", []),
    ("₺349,90", "", "TRY", [(349.9, "TRY", False)]),
    ("5 990 ₸", "", "auto", []),
    ("5 990 ₸", "", "KZT", [(5990, "KZT", False)]),
    ("฿590", "", "THB", [(590, "THB", False)]),
    # «원» распознаётся как лишний ноль: «12,9000». Не показывать ничего лучше,
    # чем показать 12,9 воны
    ("12,900원", "", "KRW", []),
    # слова, которые английская модель читает латиницей: «cym», «com», «apam»
    ("129 000 сум", "", "auto", [(129000, "UZS", False)]),
    ("1 299 сом", "", "auto", [(1299, "KGS", False)]),
    ("12 900 драм", "", "auto", [(12900, "AMD", False)]),
    ("1 299 грн", "", "auto", [(1299, "UAH", False)]),
    ("5 990 тг", "", "auto", [(5990, "KZT", False)]),
    # рубли словами и множители
    ("1 500 руб.", "", "auto", [(1500, "RUB", False)]),
    ("1500 р.", "", "auto", [(1500, "RUB", False)]),
    ("12,5 тыс. ₽", "", "auto", [(12500, "RUB", False)]),
    ("1,5 млн руб.", "", "auto", [(1500000, "RUB", False)]),
    ("$1.2M", "", "auto", [(1200000, "USD", False)]),
    ("€3.5k", "", "auto", [(3500, "EUR", False)]),
    ("$2.5 billion", "", "auto", [(2500000000, "USD", False)]),
    # несколько цен
    ("$29.99 $19.99", "strike", "auto", [(29.99, "USD", True), (19.99, "USD", False)]),
    ("$10–$20", "", "auto", [(10, "USD", False), (20, "USD", False)]),
    ("от 1 500 до 3 000 ₽", "", "auto", [(1500, "RUB", False), (3000, "RUB", False)]),
    ("Was £40 Now £25", "", "auto", [(40, "GBP", False), (25, "GBP", False)]),
    # знак между двумя числами — одного из них: к какому прижат, а если ни к
    # какому — с той стороны, где эту валюту пишут
    ("2499 $14.99", "strike", "auto", [(14.99, "USD", False)]),
    # Здесь проверяется, чей знак, а не зачёркивание: нарисованную зачёркнутую
    # «40$» после увеличения BICUBIC с усилением бледного распознавание читает
    # «403». На снимках Chrome оба приёма дают больше верного, а «40$» с линией,
    # нарисованной как в Chrome, не читалась и прежним кодом («46», «40%»).
    ("40$ 25$", "", "auto", [(40, "USD", False), (25, "USD", False)]),
    ("999 ₽ 1 299 ₽", "", "auto", [(999, "RUB", False, "999 P"), (1299, "RUB", False, "1 299 P")]),
    ("1299₽ 999₽", "", "auto", [(1299, "RUB", False, "1299P"), (999, "RUB", False, "999P")]),
    ("1 299 руб. 999 руб.", "", "auto",
     [(1299, "RUB", False, "1 299 руб."), (999, "RUB", False, "999 руб.")]),
    # старая цена читается как «i299 py6.» — её рубль не достаётся новой
    ("1299 руб. 999 руб.", "strike", "auto", [(999, "RUB", False, "999 руб.")]),
    # «руб» без точки читается как «py6», и «6» из него склеивалась с 999 в «6 999»
    ("1 299 руб 999 руб", "", "auto",
     [(1299, "RUB", False, "1 299 py6"), (999, "RUB", False, "999 руб")]),
    ("$29.99 USD", "", "auto", [(29.99, "USD", False, "$29.99 USD")]),
    # цифры, которые не деньги
    ("$4.99/mo", "", "auto", [(4.99, "USD", False)]),
    ("Save 20% — now 79,99 €", "", "auto", [(79.99, "EUR", False)]),
    # «₹» распознаётся как «%», но «%» за числом — скидка, а не рупия
    ("-15% 1 299 ₽", "", "auto", [(1299, "RUB", False)]),
    ("-15 % 1 299 ₽", "", "auto", [(1299, "RUB", False)]),
    ("Save 20% 1999", "", "auto", []),
    ("Order #12345 · $45.00", "", "auto", [(45, "USD", False)]),
    ("Order #12345 · $45.00", "", "SGD", [(45, "SGD", False)]),
    ("4.8 ★ (1,234 reviews) $59", "", "auto", [(59, "USD", False)]),
    ("4.8 ★ (1,234 reviews) $59", "", "AUD", [(59, "AUD", False)]),
    ("USB 3.0 cable – $7.99", "", "auto", [(7.99, "USD", False)]),
    ("+$5.00 shipping", "", "auto", [(5, "USD", False)]),
    ("Size 42, 2024 model", "", "EUR", []),
    # число перед «$»: знак прилип к следующему числу — количество, размер и
    # номер строки таблицы не цена. Знак после цены через пробел — цена.
    ("Qty 2 $14.99", "", "auto", [(14.99, "USD", False)]),
    ("Item 1   $7.99", "", "auto", [(7.99, "USD", False)]),
    ("Size 10 $49.99", "", "auto", [(49.99, "USD", False)]),
    ("3 $12.00", "", "auto", [(12, "USD", False)]),
    ("10 $49.99", "", "SGD", [(49.99, "SGD", False)]),
    ("14,99 $", "", "auto", [(14.99, "USD", False)]),
    # «19€99» — Франция, Бельгия: знак на месте запятой, за ним копейки той же цены
    ("19€99", "big", "auto", [(19.99, "EUR", False)]),
    ("Prix 1 299€99 TTC", "", "auto", [(1299.99, "EUR", False)]),
    ("dès 1€99", "", "auto", [(1.99, "EUR", False)]),
    ("5£49", "", "auto", [(5.49, "GBP", False)]),
    # код слитно, копейки сверху, без знака, криптовалюта
    ("USD50", "", "auto", [(50, "USD", False)]),
    ("50EUR", "", "auto", [(50, "EUR", False)]),
    # Amazon: «₹» и копейки мелко сверху, рядом цена за 100 г. Распознавание
    # читало «₹1» как «=» и теряло единицу (32 вместо 132), мелкий «₹» в
    # скобках — цифрой «2» (21 100 вместо 1 100), а «/100» считало ценой.
    ("₹132.00 (₹1,100.00 /100 g)", "file:inr_amazon.png:eng", "auto",
     [(132, "INR", False), (1100, "INR", False)]),
    ("₹132.00 (₹1,100.00 /100 g)", "file:inr_amazon.png:eng+rus", "INR",
     [(132, "INR", False), (1100, "INR", False)]),
    ("₹|132|00|(₹1,100.00 /100 g)", "amazon", "auto", [(132, "INR", False), (1100, "INR", False)]),
    # Amazon своим шрифтом (Amazon Ember): мелкий «₹» сверху сбивал и цифры —
    # «₹440» читалось «AAO», «₹418⁰⁰» — «%4.1» и «8°» (выходило 4,1 рупии), а
    # «₹» в строке — цифрой: «(2440», «Upto 213.00». Слева в первом снимке —
    # обрезок «%» от скидки: так выходит, когда цену выделяют впритык.
    ("₹440 (₹440 / kg)", "file:inr_amazon_ember.png:eng", "auto",
     [(440, "INR", False), (440, "INR", False)]),
    ("₹440.00 (₹440.00 / kg)", "file:inr_amazon_ember_cents.png:eng", "auto",
     [(440, "INR", False), (440, "INR", False)]),
    ("₹418.00 (₹418.00 / kg)", "file:inr_amazon_ember_418.png:eng", "auto",
     [(418, "INR", False), (418, "INR", False)]),
    ("Upto ₹13.00", "file:inr_amazon_ember_upto.png:eng", "auto", [(13, "INR", False)]),
    # Зачёркнутая старая цена там же: «₹» под линией читается «3» и на себя не
    # похож — знак берём по «M.R.P.» перед числом или по соседней цене.
    ("M.R.P.: ₹549", "file:inr_amazon_ember_mrp.png:eng", "auto", [(549, "INR", True)]),
    ("₹440 (₹440 / kg) M.R.P.: ₹549", "file:inr_amazon_ember_mrp_price.png:eng", "auto",
     [(440, "INR", False), (440, "INR", False), (549, "INR", True)]),
    # Arial из Chrome: зачёркнутая «1» читалась как «4» — выходило 4 299 рупий
    ("M.R.P.: ₹1,299", "file:inr_mrp_struck_arial.png:eng", "auto", [(1299, "INR", True)]),
    ("$19|99", "sup", "auto", [(19.99, "USD", False)]),
    # Франция: знак на месте запятой, копейки мелко сверху — «19€⁹⁹»
    ("19€|99", "sup", "auto", [(19.99, "EUR", False)]),
    ("1 299€|99", "sup", "auto", [(1299.99, "EUR", False)]),
    ("1 299", "", "auto", []),
    ("1 299", "", "RUB", [(1299, "RUB", False)]),
    ("0.005 BTC", "", "auto", [(0.005, "BTC", False)]),
    # так распознавание видит «€» и «₹», и так «до»: двойникам знаков верим,
    # только когда число записано как деньги
    ("Save 20% — now 79,99 ©", "", "auto", [(79.99, "EUR", False)]),
    ("© 2024 Shop", "", "EUR", []),
    ("~1,23,456.00", "", "auto", [(123456, "INR", False)]),
    ("~1,500 km", "", "auto", []),
    ("от 1 500 go 3 000 ₽", "", "auto", [(1500, "RUB", False), (3000, "RUB", False)]),
    # как в браузере (см. draw_web): серая старая цена мелко рядом с крупной
    # новой теряла точку — «$2499»; тёмная карточка со светлой страницей
    # вокруг не читалась вовсе или старая цена не считалась старой
    ("$24.99|$14.99", "web:1.5:0:3", "auto", [(24.99, "USD", True), (14.99, "USD", False)]),
    ("$24.99|$14.99", "web:1.5:0.6:12", "auto", [(24.99, "USD", True), (14.99, "USD", False)]),
    ("$29.99|$19.99", "dark:1:0:12", "auto", [(29.99, "USD", True), (19.99, "USD", False)]),
    ("$29.99|$19.99", "dark:1.25:0:12", "auto", [(29.99, "USD", True), (19.99, "USD", False)]),
    ("$24.99|$14.99", "dark:1.25:0:3", "auto", [(24.99, "USD", True), (14.99, "USD", False)]),
    ("$29.99|$19.99", "dark:1.5:0:12", "auto", [(29.99, "USD", True), (19.99, "USD", False)]),
    ("$24.99|$14.99", "dark:1.5:0.6:3", "auto", [(24.99, "USD", True), (14.99, "USD", False)]),
]


def _font(text, size, bold=False):
    if any(ord(ch) > 0x2E80 for ch in text):
        return ImageFont.truetype(os.path.join(FONTS, "msyh.ttc"), size)
    return ImageFont.truetype(os.path.join(FONTS, "segoeuib.ttf" if bold else "segoeui.ttf"),
                              size)


def draw_web(text, style):
    """Старая и новая цена «как в браузере»: стиль «web:масштаб:сдвиг:поле».

    «web» — белая карточка, «dark» — тёмная, как в магазине игр. Масштаб —
    как масштаб экрана Windows (1.25 — 125 %), сдвиг — доля пикселя, на
    которую карточка стоит не по сетке, поле — сколько страницы вокруг
    карточки попало в выделение. Страница вокруг светлая и у тёмной
    карточки: такую рамку распознавание и не переваривало.

    Chrome зачёркивает иначе, чем PIL у случая «strike»: линия на трети
    верхнего выноса шрифта над строкой, то есть ниже середины цифр, толщиной
    в 1/14 кегля. Верх линии у него на целом пикселе экрана, а толщина — нет:
    при 125 и 150 % под линией остаётся размытая кайма. Рисуем вчетверо
    крупнее и уменьшаем: так выходят и дробные позиции букв, и сглаживание.
    """
    kind, zoom, phase, margin = style.split(":")
    zoom, phase, margin = float(zoom), float(phase), int(margin)
    old, new = text.split("|")
    ss = 4
    k = zoom * ss
    if kind == "dark":
        card, c_old, c_new = (27, 40, 56), (115, 139, 149), (190, 238, 17)
        f_new = ImageFont.truetype(os.path.join(FONTS, "arialbd.ttf"), round(20 * k))
    else:
        card, c_old, c_new = (255, 255, 255), (118, 118, 118), (200, 30, 30)
        f_new = ImageFont.truetype(os.path.join(FONTS, "segoeuib.ttf"), round(22 * k))
    f_old = ImageFont.truetype(os.path.join(FONTS, "arial.ttf"), round(14 * k))
    w_old, w_new = f_old.getlength(old), f_new.getlength(new)
    asc_old, asc_new = f_old.getmetrics()[0], f_new.getmetrics()[0]
    cw, ch = int((36 + 12) * k + w_old + w_new), int((28 + 26.4) * k)
    m, off = margin * zoom * ss, phase * ss
    img = Image.new("RGB", (int(cw + 2 * m), int(ch + 2 * m)), (238, 240, 236))
    d = ImageDraw.Draw(img)
    d.rectangle((m, m, m + cw - 1, m + ch - 1), fill=card)
    base, x = m + 14 * k + off + asc_new, m + 18 * k + off
    d.text((x, base - asc_old), old, font=f_old, fill=c_old)
    t = max(ss, 14 * k * 0.073)
    top = round((base - asc_old / 3 - t / 2) / ss) * ss
    d.rectangle((x, top, x + w_old, top + t - 1), fill=c_old)
    d.text((x + w_old + 12 * k, base - asc_new), new, font=f_new, fill=c_new)
    return img.resize((img.width // ss, img.height // ss), Image.BOX)


def draw_case(text, style):
    """Цена картинкой: белый фон, шрифт сайта, как её выделили бы мышью."""
    if style.startswith(("web:", "dark:")):
        return draw_web(text, style)
    if style.startswith("file:"):
        return Image.open(os.path.join(HERE, "images", style.split(":")[1])).convert("RGB")
    pad, ink = 10, (32, 33, 36)
    img = Image.new("RGB", (600, 80), "white")
    d = ImageDraw.Draw(img)
    if style == "amazon":
        # знак и копейки мелко сверху, за ценой мелко — цена за единицу
        sign, whole, cents, unit = text.split("|")
        arial = os.path.join(FONTS, "arial.ttf")
        f_big, f_sup, f_unit = (ImageFont.truetype(arial, s) for s in (34, 15, 12))
        x = pad
        for part, font, dy, gap in ((sign, f_sup, 2, 1), (whole, f_big, 0, 1),
                                    (cents, f_sup, 2, 8), (unit, f_unit, 17, 0)):
            d.text((x, pad + dy), part, font=font, fill=ink)
            x += d.textlength(part, font=font) + gap
        right, bottom = x, pad + 42
    elif style == "sup":
        main, cents = text.split("|")
        f1, f2 = _font(main, 30, True), _font(cents, 16, True)
        d.text((pad, pad), main, font=f1, fill=ink)
        w = d.textlength(main, font=f1)
        d.text((pad + w + 1, pad + 3), cents, font=f2, fill=ink)
        right, bottom = pad + w + 1 + d.textlength(cents, font=f2), pad + 42
    elif style == "strike":
        old, new = text.split(" ", 1)
        f1, f2 = _font(old, 15), _font(new, 22, True)
        d.text((pad, pad + 6), old, font=f1, fill=(120, 120, 120))
        w = d.textlength(old, font=f1)
        d.line((pad, pad + 16, pad + w, pad + 16), fill=(120, 120, 120), width=1)
        d.text((pad + w + 10, pad), new, font=f2, fill=(200, 30, 30))
        right, bottom = pad + w + 10 + d.textlength(new, font=f2), pad + 34
    else:
        size, bold = (26, True) if style == "big" else (15, False)
        f = _font(text, size, bold)
        d.text((pad, pad), text, font=f, fill=ink)
        right, bottom = pad + d.textlength(text, font=f), pad + int(size * 1.45)
    return img.crop((0, 0, int(right) + pad, bottom + pad // 2))


# Рамку сверяем без разницы, какой азбукой прочитаны одинаковые на вид буквы:
# «1299Р» и «1299P», «руб.» и «py6.» закрывают одно и то же место.
_TWINS = str.maketrans("АВЕКМНОРСТХаеорсухб", "ABEKMHOPCTXaeopcyx6")


def main():
    bad = 0
    for text, style, source, want in CASES:
        img = draw_case(text, style)
        langs = "eng+chi_sim" if any(ord(c) > 0x2E80 for c in text) else "eng+rus"
        if style.count(":") == 2 and style.startswith("file:"):
            langs = style.split(":")[2]        # «file:снимок.png:eng» — языки как у снимка
        lines = currency.read_lines(img, langs)
        box = any(len(w) > 3 for w in want)
        got = [(round(p.amount, 6), p.code, p.strike) + ((p.text.translate(_TWINS),) if box else ())
               for p in currency.find_prices(lines, source)]
        ok = got == [(round(w[0], 6),) + tuple(w[1:3]) + tuple(t.translate(_TWINS) for t in w[3:])
                     for w in want]
        bad += not ok
        read = " | ".join(" ".join(w["text"] for w in ln) for ln in lines)
        mark = "ok" if ok else "!!"
        label = text.replace("|", "" if style == "sup" else " ") + \
            (f" {style}" if ":" in style else "")
        print(f"[{mark}] {label:28} {source:5} прочитано {read!r:30} "
              f"найдено {got}" + ("" if ok else f"   ждали {want}"))
    print(f"\nрасхождений: {bad} из {len(CASES)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
