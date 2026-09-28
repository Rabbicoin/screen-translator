import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import json, sys
res = json.load(open(f"bench_{sys.argv[1]}.json", encoding="utf-8"))
base = {(b["variant"], b["i"]): b for b in json.load(open("bench_" + (sys.argv[2] if len(sys.argv) > 2 else "base") + ".json", encoding="utf-8"))}
bad = 0
for r in res:
    want = {round(a, 4) for a, c in r["want"]}
    if any(round(a, 4) not in want or (r["want"] and c not in {c2 for _, c2 in r["want"]}) for a, c in r["got"]):
        bad += 1
        b = base[(r["variant"], r["i"])]
        if r["got"] != b["got"]:
            print("   НОВОЕ НЕВЕРНОЕ:", r["variant"][:10], r["text"], "|", b["read"], b["got"], "->", r["read"], r["got"])
print("неверная сумма или валюта:", bad)
