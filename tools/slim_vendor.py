#!/usr/bin/env python3
"""Slims the vendored Poppler/Tesseract payload down to what the app actually runs.

Upstream Windows builds of Tesseract ship unstripped (62% of the payload was DWARF
debug sections) and include the whole training toolchain plus its font-rendering
dependencies, none of which this app invokes. Running this script cuts vendor/ from
~266 MB to ~50 MB, which matters because PyInstaller is pinned to --onefile: the
whole payload is re-extracted to a temp directory on every single launch.

Re-run it after re-vendoring a new Poppler/Tesseract, or the payload creeps back up.

    python3 tools/slim_vendor.py            # report only (default)
    python3 tools/slim_vendor.py --apply    # actually strip and delete

Requires `objdump` and `objcopy` from GNU binutils, which must understand PE-COFF
(the stock Linux binutils does).

KNOWN LIMITATION: the set of DLLs to keep is derived from the *static* import
tables of the entry-point executables. A DLL loaded at runtime via LoadLibrary
would not appear there and would be deleted. The consistency check below cannot
catch that either, so an actual Windows smoke test of the built exe is required
before trusting a slimmed payload.
"""

import argparse
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Executables the application actually spawns. Everything else in bin/ is dead
# weight: pdf2image shells out to pdftoppm/pdfinfo/pdftocairo (see its source),
# and pytesseract only ever runs tesseract.exe.
#
# `drop_dlls` is deliberately off for Poppler. Removing an executable nothing
# invokes is unambiguously safe; removing a DLL is not, because the static import
# tables this script reads cannot see a LoadLibrary call. Poppler's unreachable
# DLLs are only ~2 MB and it ships already stripped, so there is nothing to win
# there worth that risk. Tesseract is the opposite case: 44 MB of unreachable
# DLLs (34 of them the ICU stack, which only its training tools use) plus 94 MB
# of debug sections.
ENTRY_POINTS = {
    'vendor/poppler/bin': {
        'entries': ['pdftoppm.exe', 'pdfinfo.exe', 'pdftocairo.exe'],
        'drop_dlls': False,
    },
    'vendor/tesseract/bin': {
        'entries': ['tesseract.exe'],
        'drop_dlls': True,
    },
}

# osd.traineddata (10 MB) drives orientation/script detection, which nothing in
# cmr_renamer calls -- no image_to_osd, no --psm 0.
TESSDATA_DIR = 'vendor/tesseract/tessdata'
TESSDATA_KEEP = ['eng.traineddata', 'ita.traineddata', 'deu.traineddata']


def _run(cmd: list) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _imports(path: str) -> list:
    """DLL names a PE binary imports, lowercased."""
    out = _run(['objdump', '-p', path])
    return [l.split()[-1].lower() for l in out.splitlines() if 'DLL Name:' in l]


def _debug_bytes(path: str) -> int:
    """Total size of .debug* sections, i.e. what --strip-debug would remove."""
    out = _run(['objdump', '-h', path])
    total = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[0].isdigit() and parts[1].startswith('.debug'):
            total += int(parts[2], 16)
    return total


def _closure(bin_dir: str, entry_points: list) -> set:
    """DLLs in `bin_dir` reachable from `entry_points` through import tables.

    DLLs not present in bin_dir are Windows system libraries and are ignored.
    """
    present = {f.lower(): f for f in os.listdir(bin_dir) if f.lower().endswith('.dll')}
    needed, queue = set(), list(entry_points)
    while queue:
        current = queue.pop()
        path = os.path.join(bin_dir, present.get(current.lower(), current))
        if not os.path.exists(path):
            print(f"   ⚠️  entry point assente: {current}")
            continue
        for dep in _imports(path):
            if dep in present and dep not in needed:
                needed.add(dep)
                queue.append(dep)
    return {present[d] for d in needed}


def _mb(paths) -> float:
    return sum(os.path.getsize(p) for p in paths) / 1048576


def _check_consistency(bin_dir: str, originali: set) -> list:
    """Every import of every kept binary must still resolve.

    An import is fine if the DLL is still in bin_dir, or if it was *never* there
    (a Windows system DLL). An import that used to be vendored and no longer is
    means the slimming went too far. Comparing against the original listing
    avoids maintaining an allowlist of system DLLs, which would produce false
    alarms on every new Windows version.
    """
    rimasti = {f.lower() for f in os.listdir(bin_dir)}
    originali_lower = {f.lower() for f in originali}
    errori = []
    for f in sorted(os.listdir(bin_dir)):
        if not f.lower().endswith(('.dll', '.exe')):
            continue
        for dep in _imports(os.path.join(bin_dir, f)):
            if dep not in rimasti and dep in originali_lower:
                errori.append(f"{f} importa {dep}, rimossa dallo slimming")
    return errori


def slim(apply: bool) -> int:
    totale_prima = totale_dopo = 0

    for rel_dir, spec in ENTRY_POINTS.items():
        entries, drop_dlls = spec['entries'], spec['drop_dlls']
        bin_dir = os.path.join(REPO, rel_dir)
        if not os.path.isdir(bin_dir):
            print(f"❌ cartella assente: {rel_dir}")
            return 1

        originali = set(os.listdir(bin_dir))
        prima = _mb([os.path.join(bin_dir, f) for f in originali])
        totale_prima += prima

        if drop_dlls:
            keep_dlls = _closure(bin_dir, entries)
            keep = {f for f in originali if f in keep_dlls or f in entries}
        else:
            # Solo gli eseguibili non invocati: le DLL restano tutte.
            keep = {f for f in originali if not f.lower().endswith('.exe') or f in entries}
        drop = originali - keep

        debug = sum(_debug_bytes(os.path.join(bin_dir, f)) for f in keep
                    if f.lower().endswith(('.dll', '.exe'))) / 1048576

        print(f"\n── {rel_dir}  ({prima:.1f} MB)"
              f"{'' if drop_dlls else '  [solo eseguibili, DLL intatte]'} ──")
        print(f"   mantenuti: {len(keep)} file, di cui {debug:.1f} MB di simboli di debug da strippare")
        print(f"   rimossi:   {len(drop)} file ({_mb([os.path.join(bin_dir, f) for f in drop]):.1f} MB)")
        for f in sorted(drop, key=lambda f: -os.path.getsize(os.path.join(bin_dir, f)))[:6]:
            print(f"      - {os.path.getsize(os.path.join(bin_dir, f))/1048576:6.1f} MB  {f}")
        if len(drop) > 6:
            print(f"      ... e altri {len(drop) - 6}")

        if apply:
            for f in drop:
                os.remove(os.path.join(bin_dir, f))
            for f in keep:
                p = os.path.join(bin_dir, f)
                if f.lower().endswith(('.dll', '.exe')):
                    tmp = p + '.stripped'
                    subprocess.run(['objcopy', '--strip-debug', p, tmp], check=True)
                    shutil.move(tmp, p)
            errori = _check_consistency(bin_dir, originali)
            if errori:
                print("\n❌ VERIFICA DI CONSISTENZA FALLITA:")
                for e in errori:
                    print(f"   {e}")
                return 1
            print("   ✅ consistenza degli import verificata")

        if apply:
            dopo = _mb([os.path.join(bin_dir, f) for f in os.listdir(bin_dir)])
        else:
            # Stima: i mantenuti meno i loro simboli di debug.
            dopo = _mb([os.path.join(bin_dir, f) for f in keep]) - debug
        totale_dopo += dopo
        print(f"   → {dopo:.1f} MB{'' if apply else ' (stimati)'}")

    # tessdata
    td = os.path.join(REPO, TESSDATA_DIR)
    if os.path.isdir(td):
        presenti = set(os.listdir(td))
        drop = {f for f in presenti if f.endswith('.traineddata') and f not in TESSDATA_KEEP}
        prima = _mb([os.path.join(td, f) for f in presenti])
        totale_prima += prima
        print(f"\n── {TESSDATA_DIR}  ({prima:.1f} MB) ──")
        for f in sorted(drop):
            print(f"   rimosso: {os.path.getsize(os.path.join(td, f))/1048576:.1f} MB  {f}")
        if apply:
            for f in drop:
                os.remove(os.path.join(td, f))
            dopo = _mb([os.path.join(td, f) for f in os.listdir(td)])
        else:
            dopo = _mb([os.path.join(td, f) for f in presenti - drop])
        totale_dopo += dopo
        print(f"   → {dopo:.1f} MB")

    verbo = "risparmiati" if apply else "risparmiabili"
    print(f"\n{'APPLICATO' if apply else 'DRY RUN'}: {totale_prima:.1f} MB → {totale_dopo:.1f} MB "
          f"({totale_prima - totale_dopo:.1f} MB {verbo}, "
          f"{100 * (totale_prima - totale_dopo) / totale_prima:.0f}%)")
    if not apply:
        print("Rieseguire con --apply per applicare.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true',
                    help="applica le modifiche (senza questo flag stampa solo il report)")
    args = ap.parse_args()
    for tool in ('objdump', 'objcopy'):
        if shutil.which(tool) is None:
            print(f"❌ '{tool}' non trovato: serve GNU binutils con supporto PE-COFF.")
            return 1
    return slim(args.apply)


if __name__ == '__main__':
    sys.exit(main())
