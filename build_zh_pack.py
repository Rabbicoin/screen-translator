# -*- coding: utf-8 -*-
"""Сборка «китайского пакета» — отдельной программы распознавания иероглифов.

Что получается: dist/ChineseOCR-<версия>.zip, внутри папка zh_ocr с zh_ocr.exe
(см. zh_ocr_helper.py) и всем, что ему нужно. Переводчик скачивает этот архив
по запросу человека и распаковывает рядом со своими настройками.

Окружение сборки живёт в build_zh/venv и в историю версий не попадает. Если его
нет, создать так (из папки проекта):

    python -m venv build_zh/venv
    build_zh/venv/Scripts/python -m pip install --no-deps rapidocr-onnxruntime==1.4.4
    build_zh/venv/Scripts/python -m pip install numpy onnxruntime opencv-python-headless ^
        Pillow pyclipper PyYAML Shapely six tqdm pyinstaller

opencv-python-headless вместо opencv-python, который просит RapidOCR: окна и
камера пакету не нужны. Отсюда --no-deps и предупреждение pip о зависимости —
это ожидаемо.

Запуск:  python build_zh_pack.py
"""

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "build_zh")
VENV_PY = os.path.join(WORK, "venv", "Scripts", "python.exe")
DIST = os.path.join(HERE, "dist")
NAME = "zh_ocr"

sys.path.insert(0, HERE)
from zh_ocr_helper import HELPER_VERSION  # noqa: E402

# Что из собранного не нужно: видео opencv (30 МБ) — пакет читает только
# картинки. Пути относительно папки программы.
DROP = ["_internal/cv2/opencv_videoio_ffmpeg*.dll"]


def main():
    if not os.path.isfile(VENV_PY):
        sys.exit(f"Нет окружения сборки {VENV_PY} — как создать, написано в начале файла.")
    out_dir = os.path.join(WORK, "dist")
    subprocess.run([VENV_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                    "--console",            # окно не появится: переводчик прячет его сам
                    "--name", NAME,
                    "--collect-data", "rapidocr_onnxruntime",   # модели и config.yaml
                    "--exclude-module", "tkinter", "--exclude-module", "matplotlib",
                    "--distpath", out_dir, "--workpath", os.path.join(WORK, "work"),
                    "--specpath", WORK, os.path.join(HERE, "zh_ocr_helper.py")],
                   check=True)
    app = os.path.join(out_dir, NAME)
    import glob
    for pattern in DROP:
        for path in glob.glob(os.path.join(app, pattern)):
            os.remove(path)
            print("убрано:", os.path.relpath(path, app))
    with open(os.path.join(app, "VERSION"), "w", encoding="utf-8") as f:
        f.write(HELPER_VERSION + "\n")

    # Проверка: собранное должно загрузить модели и ответить «готов».
    probe = subprocess.run([os.path.join(app, NAME + ".exe"), "--selftest"],
                           capture_output=True, timeout=120)
    first = probe.stdout.decode("utf-8", "replace").strip().splitlines()[:1]
    print("самопроверка:", first)
    if not first or '"ready": true' not in first[0]:
        sys.exit("Собранный пакет не запустился — архив не делаю.")

    os.makedirs(DIST, exist_ok=True)
    archive = os.path.join(DIST, f"ChineseOCR-{HELPER_VERSION}.zip")
    if os.path.exists(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, _, files in os.walk(app):
            for name in files:
                path = os.path.join(root, name)
                z.write(path, os.path.join(NAME, os.path.relpath(path, app)))
    digest = hashlib.sha256(open(archive, "rb").read()).hexdigest()
    size = sum(os.path.getsize(os.path.join(r, n)) for r, _, fs in os.walk(app) for n in fs)
    print(f"\nГотово: {archive}")
    print(f"архив {os.path.getsize(archive) / 2**20:.1f} МБ, распакованный {size / 2**20:.1f} МБ")
    print(f"SHA-256: {digest}")
    print("Эту сумму — в ZH_PACK_SHA256 в screen_translator.py.")


if __name__ == "__main__":
    main()
