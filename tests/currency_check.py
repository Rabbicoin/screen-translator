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

# (текст, стиль, валюта на экране, ожидаемое [(сумма, код, зачёркнута)])
# стиль: "" — обычный, "big" — крупная жирная цена, "sup" — копейки мелко сверху
# после «|», "strike" — первая цена зачёркнута. Пустой список — «не трогать».
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
    # редкие знаки: в «авто» не трогаем, с выбранной валютой — пересчитываем
    ("₩12,900", "", "auto", []),
    ("₩12,900", "", "KRW", [(12900, "KRW", False)]),
    ("₺349,90", "", "TRY", [(349.9, "TRY", False)]),
    ("5 990 ₸", "", "auto", []),
    ("5 990 ₸", "", "KZT", [(5990, "KZT", False)]),
    ("฿590", "", "THB", [(590, "THB", False)]),
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
    # цифры, которые не деньги
    ("$4.99/mo", "", "auto", [(4.99, "USD", False)]),
    ("Save 20% — now 79,99 €", "", "auto", [(79.99, "EUR", False)]),
    ("Order #12345 · $45.00", "", "auto", [(45, "USD", False)]),
    ("Order #12345 · $45.00", "", "SGD", [(45, "SGD", False)]),
    ("4.8 ★ (1,234 reviews) $59", "", "auto", [(59, "USD", False)]),
    ("4.8 ★ (1,234 reviews) $59", "", "AUD", [(59, "AUD", False)]),
    ("USB 3.0 cable – $7.99", "", "auto", [(7.99, "USD", False)]),
    ("+$5.00 shipping", "", "auto", [(5, "USD", False)]),
    ("Size 42, 2024 model", "", "EUR", []),
    # код слитно, копейки сверху, без знака, криптовалюта
    ("USD50", "", "auto", [(50, "USD", False)]),
    ("50EUR", "", "auto", [(50, "EUR", False)]),
    ("$19|99", "sup", "auto", [(19.99, "USD", False)]),
    ("1 299", "", "auto", []),
    ("1 299", "", "RUB", [(1299, "RUB", False)]),
    ("0.005 BTC", "", "auto", [(0.005, "BTC", False)]),
]


def _font(text, size, bold=False):
    if any(ord(ch) > 0x2E80 for ch in text):
        return ImageFont.truetype(os.path.join(FONTS, "msyh.ttc"), size)
    return ImageFont.truetype(os.path.join(FONTS, "segoeuib.ttf" if bold else "segoeui.ttf"),
                              size)


def draw_case(text, style):
    """Цена картинкой: белый фон, шрифт сайта, как её выделили бы мышью."""
    pad, ink = 10, (32, 33, 36)
    img = Image.new("RGB", (600, 80), "white")
    d = ImageDraw.Draw(img)
    if style == "sup":
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


def main():
    bad = 0
    for text, style, source, want in CASES:
        img = draw_case(text, style)
        langs = "eng+chi_sim" if any(ord(c) > 0x2E80 for c in text) else "eng+rus"
        lines = currency.read_lines(img, langs)
        got = [(round(p.amount, 6), p.code, p.strike)
               for p in currency.find_prices(lines, source)]
        ok = got == [(round(a, 6), c, s) for a, c, s in want]
        bad += not ok
        read = " | ".join(" ".join(w["text"] for w in ln) for ln in lines)
        mark = "ok" if ok else "!!"
        print(f"[{mark}] {text.replace('|', ''):28} {source:5} прочитано {read!r:30} "
              f"найдено {got}" + ("" if ok else f"   ждали {want}"))
    print(f"\nрасхождений: {bad} из {len(CASES)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
