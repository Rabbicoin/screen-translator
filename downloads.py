# -*- coding: utf-8 -*-
"""Сколько раз скачали каждую версию программы с GitHub.

Запуск: двойной клик по «Скачивания.bat».

Числа берутся у GitHub: он сам считает скачивания каждого архива, но на странице
релиза их не показывает. Счётчик обновляется с задержкой, иногда до нескольких
часов, — только что вышедшая версия может показать ноль.

Проверочные скачивания при выпуске версий вычитаются: они записаны в
downloads_self.json рядом (в историю версий не попадает). Уменьшить свой счётчик
GitHub не даёт, поэтому вычитаем здесь.
"""

import json
import os
import sys
import urllib.request

REPO = "Rabbicoin/screen-translator"
HERE = os.path.dirname(os.path.abspath(__file__))
SELF_FILE = os.path.join(HERE, "downloads_self.json")

# Консоль Windows по умолчанию не в UTF-8 — без этого русский текст рассыпается.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def own_downloads():
    """Проверочные скачивания по версиям: {"1.0.8": 1}. Нет файла — вычитать нечего."""
    try:
        with open(SELF_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k).lstrip("v"): int(v) for k, v in data.items()
                if not str(k).startswith("_")}
    except (OSError, ValueError):
        return {}


def main():
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
    request = urllib.request.Request(url, headers={"User-Agent": "ScreenTranslator-stats"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            releases = json.load(response)
    except Exception as e:
        print("Не получилось спросить GitHub:", e)
        print("Проверьте интернет и попробуйте ещё раз.")
        return 1

    own = own_downloads()
    rows, total = [], 0
    for release in releases:
        if release.get("draft"):
            continue
        version = release["tag_name"].lstrip("v")   # у первых версий тег был с «v»
        counted = sum(a.get("download_count", 0) for a in release.get("assets", []))
        real = max(0, counted - own.get(version, 0))
        total += real
        date = (release.get("published_at") or "")[:10]
        if len(date) == 10:
            date = f"{date[8:10]}.{date[5:7]}.{date[:4]}"
        rows.append((version, date, real))

    line = "  " + "-" * 30
    print()
    print(f"  {'Версия':<9}{'Вышла':<14}Скачали")
    print(line)
    for version, date, real in rows:
        print(f"  {version:<9}{date:<14}{real}")
    print(line)
    print(f"  {'Всего':<23}{total}")
    print()
    print("  Проверочные скачивания при выпуске версий не учтены.")
    print("  GitHub обновляет счётчик с задержкой: у свежей версии")
    print("  первые часы может стоять ноль.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
