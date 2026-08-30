# -*- mode: python ; coding: utf-8 -*-
"""Описание сборки для PyInstaller.

Напрямую не запускать: `build.py` перед вызовом рисует иконку, а после сборки
кладёт рядом Tesseract с языками — без него .exe распознавать не умеет.

Собирается два .exe из одного кода и с одной общей папкой библиотек:
  ScreenTranslator.exe       — обычный, без окна консоли;
  ScreenTranslator-debug.exe — с консолью и подробным логом, для разбора жалоб.
"""

a = Analysis(
    ['screen_translator.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # Эти модули находятся только в момент работы программы: бэкенд иконки в
    # трее выбирается по операционной системе, а PIL ищет Tk по строке.
    hiddenimports=[
        'pystray._win32',
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Тяжёлые пакеты, которые программе не нужны, но могут стоять в системном
    # Python и утянуться за компанию.
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy', 'pytest',
              'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'IPython'],
    noarchive=False,
    # Тот же -OO, что у самого Python: из байткода вылетают все описания
    # функций. Код из .exe всё равно достаётся — но достаётся он без
    # объяснений, почему в нём сделано именно так, а объяснения тут и есть
    # самое дорогое. Программа их не использует: `__doc__` в коде не читается,
    # assert-ов нет.
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)

exe_debug = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenTranslator-debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    exe_debug,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ScreenTranslator',
)
