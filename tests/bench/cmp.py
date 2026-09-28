import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import json, sys
a = json.load(open(f"bench_{sys.argv[1]}.json", encoding="utf-8"))
b = json.load(open(f"bench_{sys.argv[2]}.json", encoding="utf-8"))
ka = {(r["variant"], r["i"]): r for r in a}
fixed, broke = [], []
for r in b:
    o = ka.get((r["variant"], r["i"]))
    if not o: continue
    if r["ok"] and not o["ok"]: fixed.append(r)
    if o["ok"] and not r["ok"]: broke.append((o, r))
print(f"починилось {len(fixed)}, сломалось {len(broke)}")
for o, r in broke:
    print(f"  СЛОМАЛОСЬ {r['variant']:22} {r['text']!r:26} {r['source']:5} было {o['read']!r} -> стало {r['read']!r} {r['got']}")
