# -*- coding: utf-8 -*-
"""Подбор настроек сверки знаков: знаки со снимков Chrome против цифр и букв оттуда же.

Положительные — редкий знак на своём месте в ценнике («₴1 299» — первый знак,
«1 299 ₴» — последний). Отрицательные — все знаки ценников и не-цен без
редких знаков: цифры, буквы, $ € £ ¥ и прочее.
"""
import math
import os
import pickle
import sys

ARGS = sys.argv[1:]
sys.argv = sys.argv[:1]
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
import screen_translator  # noqa: E402,F401
import currency  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402
from bench import CASES, COLS, CELL_W, CELL_H, VARIANTS  # noqa: E402

SIGNS = currency.RARE_SIGNS


def prepare(crop):
    big = crop.resize((crop.width * 2, crop.height * 2), Image.BICUBIC)
    gray = currency._strip_frame(ImageOps.autocontrast(big.convert("L")))
    if currency._is_dark(gray):
        gray = ImageOps.invert(gray)
    gray = currency._boost_faint(ImageOps.autocontrast(gray))
    return currency.erase_strikes(gray, 40, 6)[0]


def collect():
    pos, neg = [], []
    for font, size, weight, dpr in VARIANTS:
        name = f"{font.replace(' ', '')}_{size}_{weight}_{dpr}"
        img = Image.open(os.path.join(HERE, "shots", name + ".png")).convert("RGB")
        for i, (text, source, want, style, group) in enumerate(CASES):
            r, c = divmod(i, COLS)
            crop = img.crop(tuple(int(round(v * dpr)) for v in (
                c * CELL_W, r * CELL_H, c * CELL_W + CELL_W - 8, r * CELL_H + CELL_H)))
            clean = prepare(crop)
            blobs = currency._blobs(clean, (0, 0, clean.width, clean.height))
            if not blobs:
                continue
            tall = max(b[3] - b[1] for b in blobs)
            blobs = [b for b in blobs if b[3] - b[1] >= 0.3 * tall]
            rare = [ch for ch in text if ch in SIGNS]
            stripped = text.replace(" ", "")
            if rare:
                ch = rare[0]
                if stripped.startswith(ch):
                    sign, rest = blobs[0], blobs[1:]
                elif stripped.endswith(ch):
                    sign, rest = blobs[-1], blobs[:-1]
                else:
                    continue
                band = currency._digit_band(rest[-4:] if stripped.endswith(ch) else rest[:4])
                if band:
                    pos.append((name, text, ch, clean, sign, band))
            elif group in ("NP", "S4", "S2", "S1", "S3"):
                band = currency._digit_band(blobs)
                if band:
                    for b in blobs:
                        neg.append((name, text, clean, b, band))
    return pos, neg


def main():
    cache = os.path.join(HERE, "tune_glyphs.pkl")
    if os.path.exists(cache) and "fresh" not in ARGS:
        pos, neg = pickle.load(open(cache, "rb"))
    else:
        pos, neg = collect()
        pickle.dump((pos, neg), open(cache, "wb"))
    print("знаков-образцов:", len(pos), "помех:", len(neg))
    rows = []
    for name, text, ch, clean, box, band in pos:
        got, margin = currency._classify_glyph(clean, box, *band)
        rows.append((ch, got, margin, name, text))
    negs = []
    for name, text, clean, box, band in neg:
        got, margin = currency._classify_glyph(clean, box, *band)
        negs.append((got, margin, name, text))
    for m in (0.0, 0.01, 0.02, 0.03, 0.04):
        ok = sum(1 for ch, got, mg, *_ in rows if got == ch and mg >= m)
        wrong = sum(1 for ch, got, mg, *_ in rows if got != ch and mg >= m)
        fp = [n for n in negs if n[1] >= m]
        print(f"порог {m:.2f}: узнано {ok}/{len(rows)}, не тот знак {wrong}, помех за знак {len(fp)}")
    per = {}
    for ch, got, mg, name, text in rows:
        p = per.setdefault(ch, [0, 0, []])
        p[1] += 1
        if got == ch and mg >= currency._SIGN_MARGIN:
            p[0] += 1
        else:
            p[2].append(f"{got}:{mg:+.3f}")
    print(" ".join(f"{ch}{p[0]}/{p[1]}" for ch, p in per.items()))
    if "v" in ARGS:
        for ch, p in per.items():
            print(ch, p[2])
        for n in sorted(negs, key=lambda n: -n[1])[:12]:
            print("  помеха", n)


if __name__ == "__main__":
    main()
