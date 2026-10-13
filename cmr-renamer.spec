# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build of cmr-renamer.exe: `uv run -- pyinstaller cmr-renamer.spec`.
#
# Equivalent to the former command line
#   pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico
#     --add-data "assets/icon.ico;assets" --add-data "vendor/poppler;vendor/poppler"
#     --add-data "vendor/tesseract;vendor/tesseract" main.py
# plus what a command line cannot do: drop what the program never loads. The
# build is --onefile, so every byte is re-extracted on every launch (see
# tools/slim_vendor.py for the same reasoning applied to vendor/ itself).

import os

import PySide6

VENDOR_DIR = os.path.abspath('vendor')

# The vendored Poppler is an MSVC build, but its folder lacks the Visual C++
# runtime that pdftoppm.exe and pdfinfo.exe import. Up to v3.1.0 a copy sat at
# the archive root (it belonged to Tk); with Qt it moved into PySide6/, and
# Poppler worked only where the PC has the VC++ Redistributable installed.
# A child process must not rely on the parent's folders anyway: Poppler gets
# its own copy, taken from the PySide6 wheel (the runtime is backward compatible,
# so a newer copy serves an older build).
VC_RUNTIME = ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
_PYSIDE_DIR = os.path.dirname(PySide6.__file__)
POPPLER_RUNTIME = [
    (os.path.join(_PYSIDE_DIR, nome), 'vendor/poppler/bin')
    for nome in os.listdir(_PYSIDE_DIR) if nome.lower() in VC_RUNTIME
]

# PyInstaller's Qt hooks collect, for QtGui alone, the software OpenGL renderer
# (opengl32sw.dll, 20 MB), ANGLE, every Qt translation and a dozen plugin
# families. This program draws plain widgets with the raster engine, sets its
# Italian labels by hand and decodes images with PIL (gui.da_pil), so it needs
# one platform plugin and the native style. The libq* names keep a Linux test
# build working; they do not exist on Windows.
QT_PLUGIN_TENUTI = {'qwindows', 'qmodernwindowsstyle', 'qwindowsvistastyle',
                    'libqxcb', 'libqoffscreen'}
# Qt6Network and Qt6Svg arrive only as dependencies of plugins dropped here:
# no kept Qt binary imports them (checked with objdump on the 6.12 wheel).
QT_FILE_SCARTATI = ('opengl32sw', 'd3dcompiler', 'libegl', 'libglesv2',
                    'qt6network', 'qt6svg')


def _serve(voce):
    destinazione = voce[0].replace('\\', '/').lower()
    sorgente = os.path.abspath(voce[1]) if voce[1] else ''
    # PyInstaller reclassifies the DLLs under vendor/ as binaries and collects
    # their dependencies a second time at the archive root: 46 identical
    # copies, 40 MB extracted per launch, that nothing loads — tesseract.exe
    # and pdftoppm.exe resolve DLLs from their own vendor/ directories.
    if sorgente.startswith(VENDOR_DIR + os.sep) and not destinazione.startswith('vendor/'):
        return False
    # Pillow's AVIF codec (8 MB); pages arrive from Poppler as PPM.
    if destinazione.startswith('pil/_avif'):
        return False
    if 'pyside6/' not in destinazione:
        return True
    nome = os.path.basename(destinazione)
    if nome.startswith(QT_FILE_SCARTATI):
        return False
    if '/translations/' in destinazione:
        return False
    if '/plugins/' in destinazione:
        return os.path.splitext(nome)[0] in QT_PLUGIN_TENUTI
    return True


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=POPPLER_RUNTIME,
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
    excludes=['tkinter', '_tkinter', 'PySide6.QtNetwork', 'PIL.AvifImagePlugin'],
    noarchive=False,
    optimize=0,
)

if os.name == 'nt' and len(POPPLER_RUNTIME) != len(VC_RUNTIME):
    raise SystemExit(f"cmr-renamer.spec: runtime VC++ per Poppler incompleto: {POPPLER_RUNTIME}")

prima = len(a.binaries) + len(a.datas)
a.binaries = [v for v in a.binaries if _serve(v)]
a.datas = [v for v in a.datas if _serve(v)]
print(f"cmr-renamer.spec: scartati {prima - len(a.binaries) - len(a.datas)} file non usati")

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
