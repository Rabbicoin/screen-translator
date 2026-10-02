# -*- coding: utf-8 -*-
"""Стенд зачёркнутых цен: старая цена под линией -> снимок headless Chrome -> read_lines -> find_prices.

Отдельно от bench.py: линия портит и знак валюты, и цифры («1» читается «4»),
и считать здесь надо не только верные ответы, но и неверные суммы — они хуже
ненайденной цены.

Запуск:   python strike.py имя            прогон, результат в bench_strike_<имя>.json
          python strike.py cmp было стало  что починилось и что сломалось между прогонами
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
SHOTS = os.path.join(HERE, "shots", "strike")

# (разметка, ожидаемое [(сумма, код, зачёркнута)], группа): pos — зачёркнутые
# рупии, neg — зачёркнутые числа без знака (ценой стать не должны), oth — прочее
CASES = [
    ("<s>₹549</s>", [(549, "INR", True)], "pos"),
    ("M.R.P.: <s>₹549</s>", [(549, "INR", True)], "pos"),
    ("M.R.P.: <s>₹1,299</s>", [(1299, "INR", True)], "pos"),
    ("M.R.P.: <s>₹2,499.00</s>", [(2499, "INR", True)], "pos"),
    ("MRP <s>₹3,999</s> ₹2,999", [(3999, "INR", True), (2999, "INR", False)], "pos"),
    ("₹440 <s>₹549</s>", [(440, "INR", False), (549, "INR", True)], "pos"),
    ("₹1,499 M.R.P.: <s>₹2,999</s>", [(1499, "INR", False), (2999, "INR", True)], "pos"),
    ("<s>₹74,999</s>", [(74999, "INR", True)], "pos"),
    ("<s>3549</s>", [], "neg"),
    ("M.R.P.: <s>3549</s>", [], "neg"),
    ("M.R.P.: <s>2549</s>", [], "neg"),
    ("M.R.P.: <s>7,549</s>", [], "neg"),
    ("MRP <s>7999</s>", [], "neg"),
    ("Было <s>2999</s>", [], "neg"),
    ("Old price: <s>7549</s>", [], "neg"),
    ("₹440 <s>2549</s>", [(440, "INR", False)], "neg"),
    ("₹440 <s>3549</s>", [(440, "INR", False)], "neg"),
    ("₹999 <s>7,499</s>", [(999, "INR", False)], "neg"),
    ("<s>1299</s> 999 ₽", [(999, "RUB", False)], "neg"),
    ("<s>2 499 ₽</s> 1 999 ₽", [(2499, "RUB", True), (1999, "RUB", False)], "oth"),
    ("<s>$29.99</s> $19.99", [(29.99, "USD", True), (19.99, "USD", False)], "oth"),
    ("<s>€49,99</s> €39,99", [(49.99, "EUR", True), (39.99, "EUR", False)], "oth"),
    ("<s>£24.99</s> £14.99", [(24.99, "GBP", True), (14.99, "GBP", False)], "oth"),
    ("List: <s>$549</s>", [(549, "USD", True)], "oth"),
]
# (шрифт, размер px, цвет, масштаб экрана): серый — как старая цена на Amazon
VARIANTS = [(f, s, c, d)
            for f, s in (("Arial", 12), ("Arial", 14), ("Segoe UI", 13), ("Verdana", 12),
                         ("Tahoma", 13), ("Georgia", 14), ("Trebuchet MS", 13))
            for c in ("#565959", "#0F1111") for d in (1.0, 1.25, 1.5)]
CELL_W, CELL_H, COLS = 300, 48, 4


def page(font, size, color):
    cells = []
    for i, (html, *_rest) in enumerate(CASES):
        r, c = divmod(i, COLS)
        cells.append(f'<div style="left:{c * CELL_W}px;top:{r * CELL_H}px">{html}</div>')
    rows = -(-len(CASES) // COLS)
    return (f"<!doctype html><meta charset=utf-8><style>body{{margin:0;background:#fff}}"
            f"div{{position:absolute;width:{CELL_W - 20}px;height:{CELL_H}px;padding-left:12px;"
            f"line-height:{CELL_H}px;font:{size}px '{font}';color:{color};white-space:nowrap}}"
            f"</style>" + "".join(cells)), COLS * CELL_W, rows * CELL_H


def shoot(v):
    font, size, color, dpr = v
    os.makedirs(SHOTS, exist_ok=True)
    name = f"{font.replace(' ', '')}_{size}_{color[1:]}_{dpr}"
    html, w, h = page(font, size, color)
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
    got = [[round(p.amount, 6), p.code, p.strike] for p in _ST.find_prices(lines)]
    want = [[round(a, 6), cd, st] for a, cd, st in want]
    return {"i": i, "html": html, "group": group, "got": got, "want": want, "ok": got == want,
            "read": " | ".join(" ".join(w["text"] for w in ln) for ln in lines),
            "variant": os.path.basename(png)[:-4]}


def wrong(r):
    """Нашлась цена, которой на картинке нет: не та сумма или не та валюта."""
    return any(p not in r["want"] for p in r["got"])


def load(name):
    return json.load(open(os.path.join(HERE, f"bench_strike_{name}.json"), encoding="utf-8"))


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
            print(f"  {title} {r['variant']:26} {r['html']!r:34} было "
                  f"{old[(r['variant'], r['i'])]['read']!r} -> стало {r['read']!r} {r['got']}")


def main():
    if sys.argv[1:2] == ["cmp"]:
        return compare(sys.argv[2], sys.argv[3])
    name = sys.argv[1] if len(sys.argv) > 1 else "run"
    jobs = []
    for v in VARIANTS:
        png = shoot(v)
        jobs += [(png, v[3], i) for i in range(len(CASES))]
    with ProcessPoolExecutor(max_workers=6, initializer=_init) as ex:
        res = list(ex.map(work, jobs, chunksize=8))
    json.dump(res, open(os.path.join(HERE, f"bench_strike_{name}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    for g in ("pos", "neg", "oth"):
        rs = [r for r in res if r["group"] == g]
        print(f"  {g:4} {sum(r['ok'] for r in rs):4} / {len(rs)}")
    bad = [r for r in res if wrong(r)]
    print(f"{name}: неверных сумм {len(bad)} —",
          dict(collections.Counter(r["html"] for r in bad).most_common()))


if __name__ == "__main__":
    main()
