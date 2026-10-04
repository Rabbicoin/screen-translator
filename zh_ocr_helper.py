# -*- coding: utf-8 -*-
"""Китайский пакет: распознавание иероглифов через RapidOCR, отдельной программой.

Переводчик скачивает пакет по запросу и запускает эту программу у себя за
спиной. Отдельно — потому что RapidOCR тянет за собой библиотеки на сотню с
лишним мегабайт, а китайский нужен не всем: основной архив от этого не растёт.

Разговор через стандартный ввод и вывод, по строке JSON на сообщение:
  ← {"ready": true, "version": "1"}                        — модели загружены
  → {"id": 7, "path": "C:\\...\\снимок.png"}               — распознай картинку
  ← {"id": 7, "lines": [{"box": [x0, y0, x1, y1], "text": "…", "score": 0.98}],
     "time": 1.4}                                          — строки и время
  ← {"id": 7, "error": "…"}                                — не вышло
Ввод закрыли — программа завершается. Модели грузятся один раз при запуске,
поэтому её держат открытой, а не запускают на каждый снимок.

Сборка: build_zh_pack.py.
"""

import json
import sys
import time

HELPER_VERSION = "1"


def _send(message):
    sys.stdout.buffer.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main():
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
        # Узкий снимок RapidOCR растягивает, пока меньшая сторона не станет 736
        # точек: полоска витрины высотой 281 раздувалась втрое, и поиск строк
        # шёл дольше всего остального. При 640 на десяти кусках витрин вышло на
        # пятую часть быстрее, текст страниц тот же; при 512 быстрее ещё, но
        # мелкие надписи на фотографиях товаров начинали читаться с ошибками.
        engine = RapidOCR(det_limit_side_len=640)
        # Первое распознавание после запуска заметно дольше следующих: движок
        # готовит вычисления. Делаем его сейчас, на картинке размером с экран, —
        # пока человек ещё выделяет область (переводчик запускает пакет по
        # нажатию горячей клавиши).
        engine(np.full((720, 1280, 3), 255, dtype=np.uint8), use_cls=False)
    except Exception as e:
        _send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1
    _send({"ready": True, "version": HELPER_VERSION})
    if "--selftest" in sys.argv:
        return 0

    for raw in sys.stdin.buffer:
        try:
            request = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        rid = request.get("id")
        try:
            image = Image.open(request["path"]).convert("RGB")
            t0 = time.perf_counter()
            # RapidOCR ждёт порядок каналов opencv: синий, зелёный, красный.
            # Проверка «не перевёрнута ли строка» снимкам экрана не нужна —
            # там текст всегда прямо, а стоит она до четверти времени.
            result, _ = engine(np.array(image)[:, :, ::-1], use_cls=False)
            lines = []
            for box, text, score in result or []:
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                lines.append({"box": [min(xs), min(ys), max(xs), max(ys)],
                              "text": text, "score": float(score)})
            _send({"id": rid, "lines": lines, "time": time.perf_counter() - t0})
        except Exception as e:
            _send({"id": rid, "error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
