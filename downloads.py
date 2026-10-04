# -*- coding: utf-8 -*-
"""Сколько раз скачали каждую версию программы с GitHub — табличкой и графиком.

Запуск: двойной клик по «Скачивания.bat». В окне печатается табличка, а в браузере
открывается «Скачивания.html»: когда выходила каждая версия и сколько её скачали,
а ниже — скачивания по дням.

Числа берутся у GitHub: он сам считает скачивания каждого архива, но на странице
релиза их не показывает. Счётчик обновляется с задержкой, иногда до нескольких
часов, — только что вышедшая версия может показать ноль.

По дням GitHub не считает, у него только общий итог. Поэтому числа каждый вечер
в 23:59 по Москве записывает сервер sevdev.ru (downloads_server.py), а программа
забирает эти записи по SSH — настройки подключения в downloads_server.json рядом,
вне истории версий. Всё вместе и сегодняшний запуск складываются в
downloads_history.json (за день одна запись — последняя), скачивания за день —
разница с предыдущей записью. Нет связи с сервером — считаем по одним своим
запускам, пропущенные дни сливаются в один отрезок. С ключом --quiet программа
только записывает числа и обновляет страницу, ничего не показывая.

Проверочные скачивания при выпуске версий вычитаются: они записаны в
downloads_self.json рядом (в историю версий не попадает). Уменьшить свой счётчик
GitHub не даёт, поэтому вычитаем здесь.
"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "Rabbicoin/screen-translator"
HERE = os.path.dirname(os.path.abspath(__file__))
SELF_FILE = os.path.join(HERE, "downloads_self.json")
SERVER_FILE = os.path.join(HERE, "downloads_server.json")
HISTORY_FILE = os.path.join(HERE, "downloads_history.json")
PAGE_FILE = os.path.join(HERE, "Скачивания.html")

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")

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


def plural(n, one, few, many):
    """plural(2, "день", "дня", "дней") → "дня"."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def how_long(delta):
    """Промежуток по-человечески: «7 часов», «2 дня»."""
    hours = delta.total_seconds() / 3600
    if round(hours) < 24:
        n = max(1, round(hours))
        return f"{n} {plural(n, 'час', 'часа', 'часов')}"
    n = max(1, round(hours / 24))
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def day_text(day):
    return f"{day.day} {MONTHS[day.month - 1]}"


def period_text(start, end):
    """Дни, когда версия была самой свежей, коротко: «08.09», «15–24.09», «30.08–02.09»."""
    if start.date() == end.date():
        return f"{start:%d.%m}"
    if start.month == end.month:
        return f"{start:%d}–{end:%d.%m}"
    return f"{start:%d.%m}–{end:%d.%m}"


# Метка версии программы: «1.1.3», «1.0.9». Прочие релизы (китайский пакет
# «zh-ocr-1») в счёт не идут.
VERSION_TAG = re.compile(r"^\d+(\.\d+)+$")


def fetch_releases():
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
    request = urllib.request.Request(url, headers={"User-Agent": "ScreenTranslator-stats"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def collect(releases, own):
    """Версии от старых к новым: номер, когда вышла (по местному времени), сколько скачали."""
    versions = []
    for release in releases:
        if release.get("draft") or not release.get("published_at"):
            continue
        version = release["tag_name"].lstrip("v")   # у первых версий тег был с «v»
        # Релиз с китайским пакетом («zh-ocr-1») — не версия программы: его
        # скачивания к счёту версий не относятся, а «свежей версией» он быть
        # не должен
        if not VERSION_TAG.match(version):
            continue
        counted = sum(a.get("download_count", 0) for a in release.get("assets", []))
        published = dt.datetime.fromisoformat(
            release["published_at"].replace("Z", "+00:00")).astimezone()
        versions.append({"version": version, "published": published, "counted": counted,
                         "real": max(0, counted - own.get(version, 0))})
    versions.sort(key=lambda v: v["published"])
    return versions


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict) and "date" in e and "counts" in e]
    except (OSError, ValueError):
        return []


def server_history():
    """Записи, которые каждый вечер делает сервер: (записи, что пошло не так или None)."""
    try:
        with open(SERVER_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return [], None   # сервер не настроен — считаем по своим запускам, это не ошибка
    command = ["ssh", "-i", cfg["key"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
               f"{cfg['user']}@{cfg['host']}", f"cat {cfg['file']}"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=40)
        if result.returncode != 0:
            reason = result.stderr.decode("utf-8", "replace").strip()
            return [], reason or f"ssh завершился с кодом {result.returncode}"
        data = json.loads(result.stdout.decode("utf-8"))
        return [e for e in data if isinstance(e, dict) and "date" in e and "counts" in e], None
    except Exception as e:
        return [], str(e)


def merge(local, remote):
    """За каждый день оставить более позднюю запись, при равном времени — серверную."""
    by_date = {e["date"]: e for e in local}
    for entry in remote:
        mine = by_date.get(entry["date"])
        if mine is None or entry.get("time", "") >= mine.get("time", ""):
            by_date[entry["date"]] = entry
    return sorted(by_date.values(), key=lambda e: e["date"])


def save_snapshot(history, versions, now):
    """Дописать сегодняшние числа GitHub. За день хранится одна запись — последняя."""
    entry = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"),
             "counts": {v["version"]: v["counted"] for v in versions}}
    history = sorted([e for e in history if e["date"] != entry["date"]] + [entry],
                     key=lambda e: e["date"])
    temp = HISTORY_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    os.replace(temp, HISTORY_FILE)
    return history


def daily(history, own):
    """Сколько скачали между соседними записями. Отрезок длиннее дня — дни без записей."""
    def real(version, count):
        return max(0, count - own.get(version, 0))

    days = []
    for prev, cur in zip(history, history[1:]):
        added = sum(max(0, real(v, c) - real(v, prev["counts"].get(v, 0)))
                    for v, c in cur["counts"].items())
        start = dt.date.fromisoformat(prev["date"]) + dt.timedelta(days=1)
        days.append({"from": start.isoformat(), "to": cur["date"],
                     "time": cur.get("time", ""), "added": added})
    return days


def page_data(versions, history, days, server_error, now):
    rows = []
    for i, v in enumerate(versions):
        nxt = versions[i + 1] if i + 1 < len(versions) else None
        until = nxt["published"] if nxt else now
        rows.append({
            "version": v["version"],
            "period": period_text(v["published"], until),
            "day": day_text(v["published"]),
            "time": v["published"].strftime("%H:%M"),
            "real": v["real"],
            "next": nxt["version"] if nxt else None,
            "fresh": how_long(until - v["published"]),
        })
    for d in days:
        d["fromText"] = day_text(dt.date.fromisoformat(d["from"]))
        d["toText"] = day_text(dt.date.fromisoformat(d["to"]))
    first = versions[0]["published"] if versions else now
    return {
        "updated": f"{day_text(now)} {now.year}, {now:%H:%M}",
        "today": now.strftime("%Y-%m-%d"),
        "total": sum(v["real"] for v in versions),
        "since": day_text(first),
        "versions": rows,
        "latestAge": how_long(now - versions[-1]["published"]) if versions else "",
        "trackingSince": day_text(dt.date.fromisoformat(history[0]["date"])) if history else "",
        "previous": ({"date": day_text(dt.date.fromisoformat(history[-2]["date"])),
                      "time": history[-2].get("time", "")} if len(history) > 1 else None),
        "days": days,
        "serverError": server_error,
    }


def write_page(data):
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    with open(PAGE_FILE, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("__DATA__", payload))


def open_page():
    try:
        os.startfile(PAGE_FILE)
    except (AttributeError, OSError):
        import webbrowser
        webbrowser.open("file:///" + PAGE_FILE.replace("\\", "/"))


def print_table(versions, days, history):
    line = "  " + "-" * 30
    print()
    print(f"  {'Версия':<9}{'Вышла':<14}Скачали")
    print(line)
    for v in reversed(versions):
        print(f"  {v['version']:<9}{v['published']:%d.%m.%Y}    {v['real']}")
    print(line)
    print(f"  {'Всего':<23}{sum(v['real'] for v in versions)}")
    print()
    if days:
        prev = history[-2]
        print(f"  С прошлого замера ({prev['date'][8:10]}.{prev['date'][5:7]}"
              f" в {prev.get('time', '')}) скачали: {days[-1]['added']}")
    else:
        print("  Счёт по дням начат сегодня. Сервер записывает числа")
        print("  каждый вечер в 23:59 — завтра будет видно, сколько скачали за день.")
    print()
    print("  Проверочные скачивания при выпуске версий не учтены.")
    print("  GitHub обновляет счётчик с задержкой: у свежей версии")
    print("  первые часы может стоять ноль.")


def main():
    quiet = "--quiet" in sys.argv[1:]
    try:
        releases = fetch_releases()
    except Exception as e:
        print("Не получилось спросить GitHub:", e)
        print("Проверьте интернет и попробуйте ещё раз.")
        return 1

    now = dt.datetime.now().astimezone()
    own = own_downloads()
    versions = collect(releases, own)
    remote, server_error = server_history()
    history = merge(load_history(), remote)
    try:
        history = save_snapshot(history, versions, now)
    except OSError as e:
        print("Не получилось записать замер:", e)
    days = daily(history, own)

    write_page(page_data(versions, history, days, server_error, now))
    if quiet:
        return 0
    print_table(versions, days, history)
    if server_error:
        print()
        print("  Не получилось забрать записи с сервера — по дням считаю")
        print("  только по запускам на этом компьютере. Причина:", server_error)
    print()
    print("  График открыт в браузере — файл «Скачивания.html».")
    open_page()
    return 0


PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Скачивания переводчика</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7;
  --surface: #fcfcfb;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --axis: #c3c2b7;
  --border: rgba(11, 11, 11, 0.10);
  --bar: #2a78d6;
  --bar-hover: #1c5cab;
  --wash: rgba(42, 120, 214, 0.12);
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d;
    --surface: #1a1a19;
    --ink: #ffffff;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --axis: #383835;
    --border: rgba(255, 255, 255, 0.10);
    --bar: #3987e5;
    --bar-hover: #6da7ec;
    --wash: rgba(57, 135, 229, 0.16);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page);
  color: var(--ink);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 880px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 24px; font-weight: 600; margin: 0 0 4px; }
.lead { color: var(--ink-2); margin: 0 0 20px; }
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.tile, .card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.tile { padding: 14px 16px; }
.tile .label { color: var(--ink-2); font-size: 13px; }
.tile .value { font-size: 32px; font-weight: 600; line-height: 1.2; margin-top: 2px; }
.tile .note { color: var(--ink-2); font-size: 13px; }
.card { padding: 20px 20px 14px; margin-top: 16px; }
h2 { font-size: 17px; font-weight: 600; margin: 0 0 2px; }
.sub { color: var(--ink-2); font-size: 13px; margin: 0 0 12px; max-width: 680px; }
.chart { position: relative; }
.chart svg { display: block; width: 100%; overflow: visible; }
.chart text { font-family: inherit; }
.v-label { fill: var(--ink); font-size: 13px; font-weight: 600; }
.x-label { fill: var(--ink-2); font-size: 12px; }
.x-label2 { fill: var(--muted); font-size: 12px; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.bar { fill: var(--bar); }
.bar.on { fill: var(--bar-hover); }
.wash { fill: var(--wash); }
.hit { fill: transparent; cursor: default; outline: none; }
.tip {
  position: absolute;
  pointer-events: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.14);
  padding: 8px 10px;
  font-size: 13px;
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.1s;
  z-index: 2;
}
.tip strong { display: block; font-size: 16px; font-weight: 600; }
.tip span { display: block; color: var(--ink-2); }
.empty { color: var(--ink-2); padding: 12px 0 6px; max-width: 620px; }
details { margin-top: 10px; font-size: 13px; color: var(--ink-2); }
summary { cursor: pointer; width: fit-content; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; margin-top: 8px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 5px 20px 5px 0; border-bottom: 1px solid var(--grid); }
th { font-weight: 600; color: var(--ink); }
td { color: var(--ink-2); }
.num { text-align: right; }
footer { color: var(--ink-2); font-size: 12px; margin-top: 16px; max-width: 680px; }
</style>
</head>
<body>
<main>
  <h1>Скачивания переводчика</h1>
  <p class="lead" id="lead"></p>

  <div class="tiles" id="tiles"></div>

  <section class="card">
    <h2>Сколько скачали каждую версию</h2>
    <p class="sub">Под столбиком — дни, пока версия была самой свежей, и её номер.
      Скачивания пришлись на эти дни, а на какой именно день — GitHub не сообщает.
      У самой свежей версии это дни с её выхода по сегодня.</p>
    <div class="chart" id="by-version"></div>
    <details>
      <summary>Числа таблицей</summary>
      <div class="table-wrap"><table id="by-version-table"></table></div>
    </details>
  </section>

  <section class="card">
    <h2>Скачивания по дням</h2>
    <p class="sub" id="by-day-sub">Каждый вечер в 23:59 по Москве сервер sevdev.ru
      записывает числа GitHub, скачивания за день — разница между соседними записями.
      Столбик «сегодня» — с прошлой записи до этого запуска, день ещё идёт. Если за
      какие-то дни записей нет, число за них одно на весь отрезок — он подсвечен
      бледной полосой.</p>
    <div class="chart" id="by-day"></div>
    <details id="by-day-details">
      <summary>Числа таблицей</summary>
      <div class="table-wrap"><table id="by-day-table"></table></div>
    </details>
  </section>

  <footer id="footer"></footer>
</main>

<script>
const DATA = __DATA__;
const NS = "http://www.w3.org/2000/svg";

function plural(n, one, few, many) {
  if (n % 10 === 1 && n % 100 !== 11) return one;
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return few;
  return many;
}
const times = n => n + " " + plural(n, "раз", "раза", "раз");

function svgEl(name, attrs, parent) {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function svgText(parent, x, y, str, cls, anchor) {
  const t = svgEl("text", { x, y, class: cls, "text-anchor": anchor || "middle" }, parent);
  t.textContent = str;
  return t;
}
function htmlEl(name, cls, str, parent) {
  const e = document.createElement(name);
  if (cls) e.className = cls;
  if (str !== undefined) e.textContent = str;
  if (parent) parent.appendChild(e);
  return e;
}

// Столбик: скруглённый верх 4px, прямой низ у нулевой линии.
function columnPath(x, base, w, h) {
  const r = Math.min(4, h, w / 2);
  return `M${x},${base} V${base - h + r} A${r},${r} 0 0 1 ${x + r},${base - h}` +
         ` H${x + w - r} A${r},${r} 0 0 1 ${x + w},${base - h + r} V${base} Z`;
}

function niceStep(max) {
  const raw = Math.max(1, max / 4);
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (m * pow >= raw) return Math.max(1, m * pow);
  return 10 * pow;
}

function showTip(box, lines, x, y) {
  const tip = box.querySelector(".tip");
  tip.replaceChildren();
  htmlEl("strong", "", lines[0], tip);
  for (const line of lines.slice(1)) htmlEl("span", "", line, tip);
  tip.style.opacity = 1;
  const left = Math.min(Math.max(0, x - tip.offsetWidth / 2), box.clientWidth - tip.offsetWidth);
  let top = y - tip.offsetHeight - 10;
  if (top < 0) top = y + 12;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
function hideTip(box) { box.querySelector(".tip").style.opacity = 0; }

/* Столбиковый график.
   items: [{ value, labels: [строка1, строка2?], tip: [...], span?: [с, по] }]
   value === null — в этот день своего числа нет (он внутри чьего-то span). */
function drawColumns(box, items, opts) {
  const width = box.clientWidth;
  if (!width) return;             // вкладка ещё не показана — нарисуем, когда появится ширина
  box.replaceChildren();
  const top = 22, plotH = opts.height;
  const bottom = opts.twoLineLabels ? 40 : 24;
  const left = opts.axis ? 30 : 0;
  const height = top + plotH + bottom;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img",
                             "aria-label": opts.title }, box);
  htmlEl("div", "tip", undefined, box);

  const max = Math.max(1, ...items.map(it => it.value || 0));
  const step = niceStep(max);
  const top_ = Math.ceil(max / step) * step;
  const base = top + plotH;
  const y = v => base - (v / top_) * plotH;
  const slot = (width - left) / items.length;
  const barW = Math.max(4, Math.min(24, slot - 6));
  const cx = i => left + slot * i + slot / 2;
  const small = slot < 38;

  if (opts.axis) {
    for (let v = step; v <= top_; v += step) {
      svgEl("line", { x1: left, x2: width, y1: y(v), y2: y(v), stroke: "var(--grid)",
                      "stroke-width": 1, "shape-rendering": "crispEdges" }, svg);
      svgText(svg, left - 8, y(v) + 4, String(v), "tick", "end");
    }
    svgText(svg, left - 8, base + 4, "0", "tick", "end");
  }
  svgEl("line", { x1: left, x2: width, y1: base, y2: base, stroke: "var(--axis)",
                  "stroke-width": 1, "shape-rendering": "crispEdges" }, svg);

  // Отрезки без ежедневных замеров — бледная полоса высотой в своё число.
  items.forEach((it, i) => {
    if (!it.span || it.value === null || it.span[0] === it.span[1]) return;
    const x0 = left + slot * it.span[0] + 2, x1 = left + slot * (it.span[1] + 1) - 2;
    const h = base - y(it.value);
    if (h > 0) svgEl("rect", { x: x0, y: base - h, width: x1 - x0, height: h, rx: 4,
                               class: "wash" }, svg);
  });

  // Какие подписи под осью оставить, чтобы не налезали друг на друга.
  const every = Math.max(1, Math.ceil((opts.twoLineLabels ? 66 : 44) / slot));
  const labelled = i => (items.length - 1 - i) % every === 0;
  const valueLabels = opts.allValues || slot >= 24;

  items.forEach((it, i) => {
    let bar = null;
    if (it.value !== null) {
      const h = base - y(it.value);
      if (h > 0) bar = svgEl("path", { d: columnPath(cx(i) - barW / 2, base, barW, h),
                                       class: "bar" }, svg);
      if (valueLabels || it.value === max)
        svgText(svg, cx(i), y(it.value) - 6, String(it.value), "v-label");
    }
    if (labelled(i)) {
      const t = svgText(svg, cx(i), base + 16, it.labels[0], "x-label");
      if (small) t.style.fontSize = "11px";
      if (it.labels[1]) {
        const t2 = svgText(svg, cx(i), base + 32, it.labels[1], "x-label2");
        if (small) t2.style.fontSize = "11px";
      }
    }
    // Область наведения — вся колонка, а не только закрашенная часть.
    const hit = svgEl("rect", { x: left + slot * i, y: top - 16, width: slot,
                                height: plotH + 16, class: "hit", tabindex: 0,
                                "aria-label": it.tip.join(", ") }, svg);
    const owner = it.value === null ? items[it.owner] : it;
    const ownerBar = () => it.value === null ? (owner || {}).bar : bar;
    const on = () => {
      if (!it.tip.length) return;
      const b = ownerBar();
      if (b) b.classList.add("on");
      const v = owner && owner.value !== null ? owner.value : 0;
      showTip(box, it.tip, cx(i), Math.min(y(v), base - 8));
    };
    const off = () => {
      const b = ownerBar();
      if (b) b.classList.remove("on");
      hideTip(box);
    };
    hit.addEventListener("pointerenter", on);
    hit.addEventListener("pointerleave", off);
    hit.addEventListener("focus", on);
    hit.addEventListener("blur", off);
    it.bar = bar;
  });
}

function fillTable(table, head, rows) {
  table.replaceChildren();
  const tr = htmlEl("tr", "", undefined, htmlEl("thead", "", undefined, table));
  head.forEach(([title, num]) => htmlEl("th", num ? "num" : "", title, tr));
  const body = htmlEl("tbody", "", undefined, table);
  rows.forEach(row => {
    const r = htmlEl("tr", "", undefined, body);
    row.forEach((cell, i) => htmlEl("td", head[i][1] ? "num" : "", String(cell), r));
  });
}

// ---------- Шапка и плитки ----------
document.getElementById("lead").textContent =
  `Данные GitHub на ${DATA.updated}. Проверочные скачивания при выпуске версий не считаются.` +
  (DATA.serverError ? " Записи с сервера забрать не удалось — по дням видны только " +
                      "запуски на этом компьютере." : "");

const tiles = document.getElementById("tiles");
function tile(label, value, note) {
  const t = htmlEl("div", "tile", undefined, tiles);
  htmlEl("div", "label", label, t);
  htmlEl("div", "value", value, t);
  htmlEl("div", "note", note, t);
}
tile("Всего скачиваний", String(DATA.total), `все версии, с ${DATA.since}`);
const latest = DATA.versions[DATA.versions.length - 1];
if (latest) tile(`Свежую версию ${latest.version} скачали`, String(latest.real),
                 `за ${DATA.latestAge} с выхода ${latest.day}`);
const lastDay = DATA.days[DATA.days.length - 1];
if (lastDay && DATA.previous)
  tile("С прошлого замера", "+" + lastDay.added, `с ${DATA.previous.date}, ${DATA.previous.time}`);
else
  tile("С прошлого замера", "—", "это первый замер — сравнивать пока не с чем");

// ---------- По версиям ----------
const versionItems = DATA.versions.map(v => ({
  value: v.real,
  labels: [v.period, v.version],
  tip: [times(v.real),
        `версия ${v.version}, вышла ${v.day} в ${v.time}`,
        v.next ? `была самой свежей ${v.fresh}, потом вышла ${v.next}`
               : `самая свежая уже ${v.fresh}`],
}));
fillTable(document.getElementById("by-version-table"),
  [["Вышла"], ["Версия"], ["Скачали", true], ["Была самой свежей"]],
  DATA.versions.slice().reverse().map(v =>
    [`${v.day}, ${v.time}`, v.version, v.real, v.next ? v.fresh : `${v.fresh} (до сих пор)`]));

// ---------- По дням ----------
function addDays(iso, n) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" +
         String(d.getDate()).padStart(2, "0");
}
const dayItems = [];
if (DATA.days.length) {
  const index = {};
  for (let d = DATA.days[0].from, i = 0; d <= DATA.today; d = addDays(d, 1), i++) {
    index[d] = i;
    dayItems.push({ value: null, labels: [d === DATA.today ? "сегодня" : d.slice(8, 10) + "." + d.slice(5, 7)],
                    tip: [] });
  }
  DATA.days.forEach(p => {
    const a = index[p.from], b = index[p.to];
    if (a === undefined || b === undefined) return;
    const today = p.to === DATA.today ? " (день ещё идёт)" : "";
    const tip = a === b
      ? ["+" + p.added, `${p.toText}${today}`, `замер в ${p.time}`]
      : ["+" + p.added, `за ${p.fromText} — ${p.toText}${today}`,
         "по отдельным дням не видно: замеров не было"];
    for (let i = a; i <= b; i++) {
      dayItems[i].tip = tip;
      dayItems[i].owner = b;
    }
    dayItems[b].value = p.added;
    dayItems[b].span = [a, b];
  });
  fillTable(document.getElementById("by-day-table"), [["Дни"], ["Скачали", true]],
    DATA.days.slice().reverse().map(p =>
      [p.from === p.to ? p.toText : `${p.fromText} — ${p.toText}`, p.added]));
} else {
  const box = document.getElementById("by-day");
  htmlEl("p", "empty",
    `Счёт по дням начат ${DATA.trackingSince}. GitHub помнит только общий итог, ` +
    "поэтому числа каждый вечер в 23:59 записывает сервер sevdev.ru. " +
    "Завтра здесь появится первый столбик.", box);
  document.getElementById("by-day-details").hidden = true;
}

function render() {
  drawColumns(document.getElementById("by-version"), versionItems,
              { height: 200, twoLineLabels: true, allValues: true,
                title: "Скачивания по версиям" });
  if (dayItems.length)
    drawColumns(document.getElementById("by-day"), dayItems,
                { height: 170, axis: true, title: "Скачивания по дням" });
}
// Перерисовать при любом изменении ширины — и когда окно сузили, и когда вкладку наконец показали.
let drawnWidth = 0;
new ResizeObserver(() => {
  const width = document.querySelector("main").clientWidth;
  if (width && width !== drawnWidth) {
    drawnWidth = width;
    render();
  }
}).observe(document.querySelector("main"));

document.getElementById("footer").textContent =
  "GitHub обновляет счётчик с задержкой: у только что вышедшей версии первые часы может " +
  "стоять ноль. Копия записей по дням — в downloads_history.json рядом с программой.";
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
