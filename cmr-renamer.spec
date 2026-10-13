# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build of cmr-renamer.exe: `uv run -- pyinstaller cmr-renamer.spec`.
#
# Equivalent to the former command line
#   pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico
#     --add-data "assets/icon.ico;assets" --add-data "vendor/poppler;vendor/poppler"
#     --add-data "vendor/tesseract;vendor/tesseract" main.py
# plus one thing a command line cannot do: drop the parts of Qt this program
# never loads. The build is --onefile, so every byte is re-extracted on every
# launch (see tools/slim_vendor.py for the same reasoning applied to vendor/).

import os

# PyInstaller's Qt hooks collect, for QtGui alone, the software OpenGL renderer
# (opengl32sw.dll, 20 MB), ANGLE, every Qt translation and a dozen plugin
# families. This program draws plain widgets with the raster engine, sets its
# Italian labels by hand and decodes images with PIL (gui.da_pil), so it needs
# exactly two plugin families: the platform integration and the native style.
QT_PLUGIN_TENUTI = {'platforms', 'styles'}
QT_FILE_SCARTATI = ('opengl32sw', 'd3dcompiler', 'libegl', 'libglesv2')


def _serve(voce):
    destinazione = voce[0].replace('\\', '/').lower()
    if 'pyside6/' not in destinazione:
        return True
    nome = os.path.basename(destinazione)
    if nome.startswith(QT_FILE_SCARTATI):
        return False
    if '/translations/' in destinazione:
        return False
    if '/plugins/' in destinazione:
        famiglia = destinazione.split('/plugins/', 1)[1].split('/', 1)[0]
        return famiglia in QT_PLUGIN_TENUTI
    return True


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/icon.ico', 'assets'),
        ('vendor/poppler', 'vendor/poppler'),
        ('vendor/tesseract', 'vendor/tesseract'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The GUI is Qt now: Tcl/Tk would only be several MB of extraction per launch.
    # QtNetwork is pulled in by PySide6/__init__.py's module list, not by any
    # import of ours (the updater uses urllib).
    excludes=['tkinter', '_tkinter', 'PySide6.QtNetwork'],
    noarchive=False,
    optimize=0,
)

prima = len(a.binaries) + len(a.datas)
a.binaries = [v for v in a.binaries if _serve(v)]
a.datas = [v for v in a.datas if _serve(v)]
print(f"cmr-renamer.spec: scartati {prima - len(a.binaries) - len(a.datas)} file Qt non usati")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='cmr-renamer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
