# -*- coding: utf-8 -*-
"""Записывает скачивания переводчика с GitHub — на сервере, раз в сутки.

Живёт на сервере sevdev.ru в ~/downloads-stats/ и запускается расписанием
(crontab пользователя sevdev) каждый день в 23:59 по Москве. Спрашивает GitHub,
сколько раз скачали каждую версию, и дописывает числа в history.json рядом.
За день одна запись. Сервер работает круглосуточно, поэтому дни не пропадают,
даже когда компьютер выключен.

«Скачивания.bat» на компьютере забирает history.json по SSH и рисует по нему
график. Формат записей тот же, что у downloads_history.json.
"""

import datetime as dt
import json
import os
import time
import urllib.request
from zoneinfo import ZoneInfo

REPO = "Rabbicoin/screen-translator"
HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(HERE, "history.json")
MOSCOW = ZoneInfo("Europe/Moscow")


def fetch_counts():
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
    request = urllib.request.Request(url, headers={"User-Agent": "ScreenTranslator-stats"})
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    return {r["tag_name"].lstrip("v"): sum(a.get("download_count", 0) for a in r.get("assets", []))
            for r in releases if not r.get("draft")}


def main():
    # Время берём до запроса: если GitHub ответит только после полуночи,
    # запись всё равно должна лечь на уходящий день.
    now = dt.datetime.now(MOSCOW)
    for attempt in range(3):
        try:
            counts = fetch_counts()
            break
        except Exception as e:
            print(f"{now:%Y-%m-%d %H:%M} GitHub не ответил (попытка {attempt + 1}): {e}")
            time.sleep(60)
    else:
        return 1

    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    except (OSError, ValueError):
        history = []
    entry = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "counts": counts}
    history = sorted([e for e in history if e.get("date") != entry["date"]] + [entry],
                     key=lambda e: e["date"])
    temp = HISTORY_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    os.replace(temp, HISTORY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
