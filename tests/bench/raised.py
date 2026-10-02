# -*- coding: utf-8 -*-
"""Стенд цен с мелким знаком и копейками сверху, как на Amazon: «₹440⁰⁰», «$19⁹⁹».

Отдельно от bench.py: мелкий знак сбивает распознавание цифр за ним («₹440» ->
«AAO», «₹410»), а копейки без точки склеиваются с числом («$1999» — в сто раз
дороже). Считаем и верные ответы, и неверные суммы.

Запуск:   python raised.py имя            прогон, результат в bench_raised_<имя>.json
          python raised.py cmp было стало  что починилось и что сломалось между прогонами
Код берётся из папки REPO (переменная окружения), по умолчанию — из этой же.
"""
import collections
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(HERE)))
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = os.path.join(HERE, "shots", "raised")
UP = "<span class=u>{}</span>"


def price(sign, whole, cents=None):
    """Цена, как её верстает Amazon: знак и копейки мелко и приподняты."""
    return UP.format(sign) + whole + (UP.format(cents) if cents else "")


# (разметка, ожидаемое [(сумма, код)], группа): inr — рупии с копейками, inr0 —
# без копеек, oth — доллар, евро, фунт, rare — другие редкие знаки, neg — не цены
CASES = [
    ("2 options from " + price("₹", "440", "00"), [(440, "INR")], "inr"),
    (price("₹", "440", "00") + " FREE", [(440, "INR")], "inr"),
    (price("₹", "1,299", "00"), [(1299, "INR")], "inr"),
    ("from " + price("₹", "13", "00"), [(13, "INR")], "inr"),
    (price("₹", "74,999", "00"), [(74999, "INR")], "inr"),
    (price("₹", "959", "50"), [(959.5, "INR")], "inr"),
    (price("₹", "440"), [(440, "INR")], "inr0"),
    ("from " + price("₹", "2,877"), [(2877, "INR")], "inr0"),
    (price("$", "19", "99"), [(19.99, "USD")], "oth"),
    ("from " + price("$", "1,299", "00"), [(1299, "USD")], "oth"),
    (price("€", "19", "99"), [(19.99, "EUR")], "oth"),
    (price("£", "24", "99"), [(24.99, "GBP")], "oth"),
    (price("₪", "45", "90"), [(45.9, "ILS")], "rare"),
    (price("₱", "199", "00"), [(199, "PHP")], "rare"),
    (price("฿", "199", "00"), [(199, "THB")], "rare"),
    (price("₺", "440", "00"), [(440, "TRY")], "rare"),
    (price("*", "19", "99"), [], "neg"),
    (price("'", "24", "00"), [], "neg"),
    (price('"', "9", "00"), [], "neg"),
    (price("°", "15", "30"), [], "neg"),
    (price("™", "440", "00"), [], "neg"),
    (price("7", "440", "00"), [], "neg"),
    (price("2", "199", "50"), [], "neg"),
    (price("T", "440", "00"), [], "neg"),
    (price("?", "440", "00"), [], "neg"),
    (price("z", "440", "00"), [], "neg"),
    ("с 9" + UP.format("00") + " до 18" + UP.format("00"), [], "neg"),
    (price("№", "12", "34"), [], "neg"),
]
VARIANTS = [(f, s, d) for f in ("Arial", "Segoe UI", "Verdana", "Tahoma", "Georgia", "Trebuchet MS")
            for s in (16, 18, 21, 28) for d in (1.0, 1.25, 1.5)]
CELL_W, CELL_H, COLS = 320, 56, 4


def page(font, size):
    cells = []
    for i, (html, *_rest) in enumerate(CASES):
        r, c = divmod(i, COLS)
        cells.append(f'<div style="left:{c * CELL_W}px;top:{r * CELL_H + 12}px">{html}</div>')
    rows = -(-len(CASES) // COLS)
    return (f"<!doctype html><meta charset=utf-8><style>body{{margin:0;background:#fff}}"
            f"div{{position:absolute;width:{CELL_W - 20}px;padding-left:12px;"
            f"font:{size}px '{font}';color:#0F1111;white-space:nowrap}}"
            f".u{{font-size:.6em;position:relative;top:-.5em}}</style>"
            + "".join(cells)), COLS * CELL_W, rows * CELL_H


def shoot(v):
    font, size, dpr = v
    os.makedirs(SHOTS, exist_ok=True)
    name = f"{font.replace(' ', '')}_{size}_{dpr}"
    html, w, h = page(font, size)
    hp, png = os.path.join(SHOTS, name + ".html"), os.path.join(SHOTS, name + ".png")
    if not os.path.exists(png) or os.environ.get("RESHOOT") == "1":
        open(hp, "w", encoding="utf-8").write(html)
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--force-device-scale-factor={dpr}", f"--window-size={w},{h}",
                        f"--screenshot={png}", "file:///" + hp.replace("\\", "/")],
                       check=True, capture_output=True, timeout=60)
    return png


_ST = None


def _init():
    global _ST
    sys.path.insert(0, REPO)
    sys.argv = sys.argv[:1]
    import screen_translator  # noqa: F401  (tesseract и языки)
    import currency
    _ST = currency


def work(job):
    png, dpr, i = job
    from PIL import Image
    html, want, group = CASES[i]
    r, c = divmod(i, COLS)
    img = Image.open(png).convert("RGB")
    crop = img.crop(tuple(int(round(v * dpr)) for v in (
        c * CELL_W, r * CELL_H, c * CELL_W + CELL_W - 8, r * CELL_H + CELL_H)))
    lines = _ST.read_lines(crop, "eng", 2.0)
    got = [[round(p.amount, 6), p.code] for p in _ST.find_prices(lines)]
    want = [[round(a, 6), cd] for a, cd in want]
    return {"i": i, "html": html, "group": group, "got": got, "want": want, "ok": got == want,
            "read": " | ".join(" ".join(w["text"] for w in ln) for ln in lines),
            "variant": os.path.basename(png)[:-4]}


def wrong(r):
    """Нашлась цена, которой на картинке нет: не та сумма или не та валюта."""
    return any(p not in r["want"] for p in r["got"])


def load(name):
    return json.load(open(os.path.join(HERE, f"bench_raised_{name}.json"), encoding="utf-8"))


def compare(a, b):
    old = {(r["variant"], r["i"]): r for r in load(a)}
    new = load(b)
    fixed = [r for r in new if r["ok"] and not old[(r["variant"], r["i"])]["ok"]]
    broke = [r for r in new if not r["ok"] and old[(r["variant"], r["i"])]["ok"]]
    worse = [r for r in new if wrong(r) and not wrong(old[(r["variant"], r["i"])])]
    print(f"починилось {len(fixed)}, сломалось {len(broke)}; неверных сумм: "
          f"{sum(map(wrong, old.values()))} -> {sum(map(wrong, new))}, новых {len(worse)}")
    for title, rows in (("СЛОМАЛОСЬ", broke), ("НОВАЯ НЕВЕРНАЯ", worse)):
        for r in rows:
            print(f"  {title} {r['variant']:22} случай {r['i']:2} было "
                  f"{old[(r['variant'], r['i'])]['read']!r} -> стало {r['read']!r} {r['got']}")


def main():
    if sys.argv[1:2] == ["cmp"]:
        return compare(sys.argv[2], sys.argv[3])
    name = sys.argv[1] if len(sys.argv) > 1 else "run"
    jobs = []
    for v in VARIANTS:
        png = shoot(v)
        jobs += [(png, v[2], i) for i in range(len(CASES))]
    with ProcessPoolExecutor(max_workers=6, initializer=_init) as ex:
        res = list(ex.map(work, jobs, chunksize=8))
    json.dump(res, open(os.path.join(HERE, f"bench_raised_{name}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    for g in ("inr", "inr0", "oth", "rare", "neg"):
        rs = [r for r in res if r["group"] == g]
        print(f"  {g:4} {sum(r['ok'] for r in rs):4} / {len(rs)}")
    bad = [r for r in res if wrong(r)]
    print(f"{name}: неверных сумм {len(bad)} — по случаям:",
          dict(collections.Counter(r["i"] for r in bad).most_common()))


if __name__ == "__main__":
    main()
