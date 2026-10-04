# -*- coding: utf-8 -*-
"""Стенд разметки: папка снимков -> блоки распознавания -> JSON, и сравнение прогонов.

Нужен, чтобы правка разметки (стирание рамок, склейка абзацев, ячейки) не
ломала то, что уже читалось: прогоняем папку снимков до правки и после и
смотрим, какие блоки появились, пропали или склеились иначе.

Запуск:  python blocks.py прогон <папка со снимками>     -> blocks_<прогон>.json
         python blocks.py сравнить <было> <стало>          -> что изменилось
Снимки в историю версий не кладём: это чужие экраны. Хранить их удобно в
папке вне проекта и передавать путём.
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("REPO", os.path.dirname(os.path.dirname(HERE)))


def run(name, folder):
    os.environ.setdefault("ST_DEBUG", "0")
    sys.path.insert(0, REPO)
    os.chdir(REPO)
    import screen_translator as st
    from PIL import Image

    out = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.png"))):
        img = Image.open(path).convert("RGB")
        t0 = time.perf_counter()
        try:
            _, blocks, langs = st.ocr_blocks(img)
        except Exception as e:                        # стенд, не программа
            out[os.path.basename(path)] = {"error": repr(e)}
            continue
        secs = round(time.perf_counter() - t0, 2)
        out[os.path.basename(path)] = {
            "langs": langs, "secs": secs,
            "blocks": [{"bbox": list(b["bbox"]), "lines": b["lines"],
                        "font_h": round(b["font_h"], 1), "text": b["text"]}
                       for b in blocks]}
        print(f"{os.path.basename(path)}: блоков {len(blocks)}, {secs} с", flush=True)
    with open(os.path.join(HERE, f"blocks_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def compare(before, after):
    a = json.load(open(os.path.join(HERE, f"blocks_{before}.json"), encoding="utf-8"))
    b = json.load(open(os.path.join(HERE, f"blocks_{after}.json"), encoding="utf-8"))
    for name in sorted(a):
        was = [x["text"] for x in a[name].get("blocks", [])]
        now = [x["text"] for x in b.get(name, {}).get("blocks", [])]
        if was == now:
            print(f"== {name}: без изменений ({len(was)} блоков)")
            continue
        print(f"## {name}: {len(was)} -> {len(now)} блоков")
        for t in was:
            if t not in now:
                print("   -", repr(t))
        for t in now:
            if t not in was:
                print("   +", repr(t))
    secs = lambda d: round(sum(v.get("secs", 0) for v in d.values()), 1)
    print(f"время: {secs(a)} с -> {secs(b)} с")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "сравнить":
        compare(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 3:
        run(sys.argv[1], sys.argv[2])
    else:
        print(__doc__)
