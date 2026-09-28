import sys, os
ARGS = sys.argv[1:]
sys.argv = sys.argv[:1]
sys.path.insert(0, os.environ.get("REPO", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
import screen_translator, currency
from PIL import Image
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
from bench import CASES, COLS, CELL_W, CELL_H
shot, text = ARGS
dpr = float(shot.rsplit("_", 1)[1])
img = Image.open(os.path.join(SP, "shots", shot + ".png")).convert("RGB")
i = [n for n, c in enumerate(CASES) if c[0] == text][0]
r, cc = divmod(i, COLS)
crop = img.crop(tuple(int(round(v*dpr)) for v in (cc*CELL_W, r*CELL_H, cc*CELL_W+CELL_W-8, r*CELL_H+CELL_H)))
crop.resize((crop.width*3, crop.height*3)).save(os.path.join(SP, "trace.png"))
orig_edge, orig_cls = currency._edge_sign, currency._classify_glyph
def cls(clean, box, *a):
    res = orig_cls(clean, box, *a); print("   сверка", box, res); return res
def edge(clean, glyph, tall, side, *a):
    res = orig_edge(clean, glyph, tall, side, *a); print("  край", side, glyph, "куски", tall, "->", res); return res
currency._classify_glyph = cls; currency._edge_sign = edge
lines = currency.read_lines(crop, "eng", 2.0)
print([(w["text"], w["box"]) for l in lines for w in l])
