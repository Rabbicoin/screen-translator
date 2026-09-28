# -*- coding: utf-8 -*-
"""Стенд в Chrome для пересчёта цен: ценники -> снимок headless Chrome -> read_lines -> find_prices.

Запуск:  python bench.py [имя_прогона] [языки]
Результат: bench_<имя>.json и сводка в консоли.
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, HERE)
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = os.path.join(HERE, "shots")

from cases import S1, S2, S3, S4  # noqa: E402

# редкие знаки в разных записях: ради них стенд и собран
RARE = [
    ("UAH", "₴"), ("INR", "₹"), ("ILS", "₪"), ("PHP", "₱"), ("VND", "₫"), ("GEL", "₾"),
    ("AMD", "֏"), ("AZN", "₼"), ("MNT", "₮"), ("BTC", "₿"), ("KRW", "₩"), ("TRY", "₺"),
    ("KZT", "₸"), ("THB", "฿"),
]
EXTRA = []
for code, s in RARE:
    EXTRA += [(f"{s}1 299", "auto", [(1299, code)], "", "rare"),
              (f"1 299 {s}", "auto", [(1299, code)], "", "rare"),
              (f"Цена: 45{s}", "auto", [(45, code)], "", "rare")]

# не цены: знак здесь находить нельзя
NOT_PRICES = [
    "100 T-shirts", "5 W", "Size 42 d", "2024", "iPhone 15", "12 m", "3 F", "Order 1002",
    "B100", "6100 rpm", "Page 3 of 10", "Room 2B", "Win 10 Pro", "Level 5 P", "12 Mbps",
    "7 days", "Model X100", "4K 60 Hz", "Vol. 2", "No 8", "A4 80 g", "Rated 4.5",
    "50 шт.", "Дом 12 к. 2", "кв. 45", "10 ч", "5 л", "до 20 лет", "3 дня", "Табл. 3",
    "1:0", "8 GB RAM", "Top 10", "x2", "2 × 3", "15 min", "g 250", "E 45", "t 12",
]
CASES = ([(t, s, w, st, "S1") for t, s, w, st, _ in S1]
         + [(t, s, w, st, "S2") for t, s, w, st, _ in S2]
         + [(t, s, w, st, "S3") for t, s, w, st, _ in S3]
         + [(t, s, w, st, "S4") for t, s, w, st, _ in S4 if st != "strike"]
         + [(t, s, w, st, g) for t, s, w, st, g in EXTRA]
         + [(t, "auto", [], "", "NP") for t in NOT_PRICES])

VARIANTS = [  # (шрифт, размер px, жирность, масштаб экрана)
    ("Arial", 16, 400, 1.0),
    ("Segoe UI", 15, 400, 1.25),
    ("Georgia", 18, 400, 1.0),
    ("Tahoma", 14, 700, 1.25),
    # шрифтов ниже среди образцов нет — как сайт с незнакомым шрифтом
    ("Verdana", 15, 400, 1.0),
    ("Trebuchet MS", 16, 400, 1.25),
]
CELL_W, CELL_H, COLS = 300, 56, 4


def page(font, size, weight):
    cells = []
    for i, (text, *_rest) in enumerate(CASES):
        r, c = divmod(i, COLS)
        cells.append(f'<div style="left:{c * CELL_W}px;top:{r * CELL_H}px">'
                     f'{text.replace("&", "&amp;").replace("<", "&lt;")}</div>')
    rows = -(-len(CASES) // COLS)
    return (f"<!doctype html><meta charset=utf-8><style>body{{margin:0;background:#fff}}"
            f"div{{position:absolute;width:{CELL_W - 20}px;height:{CELL_H}px;padding-left:12px;"
            f"line-height:{CELL_H}px;font:{weight} {size}px '{font}';color:#202124;"
            f"white-space:nowrap}}</style>"
            + "".join(cells)), COLS * CELL_W, rows * CELL_H


def shoot(v):
    font, size, weight, dpr = v
    os.makedirs(SHOTS, exist_ok=True)
    name = f"{font.replace(' ', '')}_{size}_{weight}_{dpr}"
    html, w, h = page(font, size, weight)
    hp = os.path.join(SHOTS, name + ".html")
    open(hp, "w", encoding="utf-8").write(html)
    png = os.path.join(SHOTS, name + ".png")
    if os.path.exists(png) and os.path.getmtime(png) > os.path.getmtime(hp) - 1 \
            and os.environ.get("RESHOOT") != "1" and os.path.getsize(png) > 0:
        return png
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
    png, dpr, i, langs = job
    from PIL import Image
    currency = _ST
    text, source, want, style, group = CASES[i]
    r, c = divmod(i, COLS)
    img = Image.open(png).convert("RGB")
    box = tuple(int(round(v * dpr)) for v in (c * CELL_W, r * CELL_H, c * CELL_W + CELL_W - 8,
                                             r * CELL_H + CELL_H))
    crop = img.crop(box)
    t0 = time.perf_counter()
    lines = currency.read_lines(crop, langs, 2.0)
    got = [(round(p.amount, 6), p.code) for p in currency.find_prices(lines, source)]
    dt = time.perf_counter() - t0
    ok = got == [(round(a, 6), cd) for a, cd in want]
    read = " | ".join(" ".join(w["text"] for w in ln) for ln in lines)
    return {"i": i, "text": text, "source": source, "group": group, "ok": ok, "got": got,
            "want": want, "read": read, "t": dt}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "run"
    langs = sys.argv[2] if len(sys.argv) > 2 else "eng"
    jobs = []
    for v in VARIANTS:
        png = shoot(v)
        jobs += [(png, v[3], i, langs) for i in range(len(CASES))]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=6, initializer=_init) as ex:
        res = list(ex.map(work, jobs, chunksize=8))
    for r, (png, *_x) in zip(res, jobs):
        r["variant"] = os.path.basename(png)[:-4]
    json.dump(res, open(os.path.join(HERE, f"bench_{name}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    groups = {}
    for r in res:
        g = groups.setdefault(r["group"], [0, 0])
        g[0] += r["ok"]
        g[1] += 1
    print(f"{name} ({langs}): верно {sum(r['ok'] for r in res)} из {len(res)} "
          f"за {time.perf_counter() - t0:.0f} с, в среднем {sum(r['t'] for r in res) / len(res):.2f} с на снимок")
    for g, (ok, n) in sorted(groups.items()):
        print(f"  {g:5} {ok:4} / {n}")


if __name__ == "__main__":
    main()
