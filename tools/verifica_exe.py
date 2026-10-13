"""Controllo dell'exe prodotto dalla CI, da fare prima di firmarlo con tools/firma.py.

L'aggiornamento automatico installa solo exe firmati: se si firma solo dopo questo controllo, un exe
a cui `cmr-renamer.spec` ha tolto qualcosa di necessario non arriva mai ai PC. Un exe rotto non
potrebbe nemmeno aggiornarsi da solo, e andrebbe sostituito a mano.

    uv run --with pyinstaller python tools/verifica_exe.py v3.2.0 v3.2.1

Gli argomenti sono la release precedente (già provata sul campo) e quella nuova, come tag GitHub
(scaricati con `gh`) o come percorsi di exe locali. Serve `objdump` (GNU binutils), come per
tools/slim_vendor.py. Esce con 1 se trova un problema.

Cosa controlla:
- **Regressioni di dipendenze.** Per ogni DLL/pyd/exe presente in entrambe le versioni, ogni DLL
  importata che nella precedente si trovava dentro l'archivio (nella cartella del binario o nella
  radice, l'ordine di ricerca di Windows per un exe estratto da PyInstaller) deve trovarsi anche
  nella nuova. Le DLL di sistema non contano: non sono nell'archivio in nessuna delle due.
- **File indispensabili** presenti: tesseract.exe, pdftoppm.exe, i traineddata, l'icona e il
  plugin Qt `qwindows`.
- **Cartelle di vendor/ autosufficienti.** tesseract.exe e pdftoppm.exe girano come processi
  separati e cercano le DLL nella propria cartella e in System32, non nella radice dell'exe: ogni
  DLL che un binario di vendor/ importa deve stare nella sua cartella o essere di sistema
  (`DLL_DI_SISTEMA`). Così si è scoperto che a Poppler mancava il runtime di Visual C++.
- **Copie di vendor/ nella radice**, il difetto che `cmr-renamer.spec` toglie: se ricompaiono, il
  filtro ha smesso di funzionare.

Limite: legge le tabelle di import, quindi non vede un `LoadLibrary` a runtime (i plugin Qt sono
caricati così: per questo `qwindows` è nella lista dei file indispensabili). Non sostituisce una
prova dell'exe su Windows, ma ferma prima della firma gli errori del filtro.
"""

import os
import shutil
import subprocess
import sys
import tempfile

from PyInstaller.archive.readers import CArchiveReader

ASSET = "cmr-renamer.exe"
INDISPENSABILI = (
    "vendor/tesseract/bin/tesseract.exe",
    "vendor/poppler/bin/pdftoppm.exe",
    "vendor/tesseract/tessdata/ita.traineddata",
    "vendor/tesseract/tessdata/deu.traineddata",
    "vendor/tesseract/tessdata/eng.traineddata",
    "assets/icon.ico",
    "PySide6/plugins/platforms/qwindows.dll",
)


# DLL che ogni Windows supportato ha in System32 (più le api-ms-win-*, controllate a parte).
# Il runtime di Visual C++ (msvcp140, vcruntime140) non c'è: si installa a parte.
DLL_DI_SISTEMA = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll",
    "oleaut32.dll", "ws2_32.dll", "crypt32.dll", "msvcrt.dll", "ucrtbase.dll", "bcrypt.dll",
    "secur32.dll", "shlwapi.dll", "comdlg32.dll", "version.dll", "winmm.dll", "userenv.dll",
    "ntdll.dll", "iphlpapi.dll", "wldap32.dll", "normaliz.dll", "dnsapi.dll", "rpcrt4.dll",
    "usp10.dll", "msimg32.dll", "dwrite.dll", "d2d1.dll", "setupapi.dll", "imm32.dll",
    "comctl32.dll", "winspool.drv", "psapi.dll", "dbghelp.dll",
}


def _scarica(arg, cartella):
    """Percorso locale dell'exe: `arg` se è un file, altrimenti lo scarica dalla release `arg`."""
    if os.path.isfile(arg):
        return arg
    dest = os.path.join(cartella, arg)
    os.makedirs(dest)
    subprocess.run(["gh", "release", "download", arg, "-p", ASSET, "-D", dest], check=True)
    return os.path.join(dest, ASSET)


def _import(dati, cartella):
    """DLL importate da un binario PE, in minuscolo (objdump legge anche PE da Linux)."""
    percorso = os.path.join(cartella, "binario")
    with open(percorso, "wb") as f:
        f.write(dati)
    uscita = subprocess.run(["objdump", "-p", percorso], capture_output=True, text=True).stdout
    return {riga.split()[-1].lower() for riga in uscita.splitlines() if "DLL Name" in riga}


def analizza(exe, cartella):
    """Per un exe: {binario: DLL importate trovate nell'archivio}, {binario di vendor/: DLL che non
    trova}, insieme di tutti i percorsi, dimensione."""
    archivio = CArchiveReader(exe)
    percorsi = {n.replace("\\", "/"): n for n in archivio.toc}
    per_cartella = {}
    for p in percorsi:
        per_cartella.setdefault(os.path.dirname(p).lower(), set()).add(os.path.basename(p).lower())
    risolte, irrisolte_vendor = {}, {}
    for p, nome in percorsi.items():
        if not p.lower().endswith((".dll", ".pyd", ".exe")):
            continue
        importate = _import(archivio.extract(nome), cartella)
        propria = per_cartella.get(os.path.dirname(p).lower(), set())
        risolte[p] = importate & (propria | per_cartella.get("", set()))
        if p.startswith("vendor/"):
            mancanti = {d for d in importate - propria
                        if d not in DLL_DI_SISTEMA and not d.startswith(("api-ms-win-", "ext-ms-win-"))}
            if mancanti:
                irrisolte_vendor[p] = mancanti
    return risolte, irrisolte_vendor, set(percorsi), os.path.getsize(exe)


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    if shutil.which("objdump") is None:
        print("❌ objdump non trovato: installa GNU binutils.")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        prima, dopo = (_scarica(a, tmp) for a in argv[1:])
        vecchio, _, _, peso_vecchio = analizza(prima, tmp)
        nuovo, irrisolte_vendor, percorsi, peso_nuovo = analizza(dopo, tmp)

    problemi = 0
    for p, dll in sorted(nuovo.items()):
        perse = vecchio.get(p, dll) - dll
        if perse:
            problemi += 1
            print(f"❌ {p} non trova più: {', '.join(sorted(perse))}")
    for p, mancanti in sorted(irrisolte_vendor.items()):
        # poppler-glib.dll (gio/glib) non è usata: pdftoppm e pdfinfo non la importano.
        if os.path.basename(p).lower() == "poppler-glib.dll":
            continue
        problemi += 1
        print(f"❌ {p} cerca fuori dalla sua cartella: {', '.join(sorted(mancanti))}")
    for p in INDISPENSABILI:
        if p not in percorsi:
            problemi += 1
            print(f"❌ manca {p}")
    vendor = {os.path.basename(p).lower() for p in percorsi if p.startswith("vendor/")}
    doppie = sorted(p for p in percorsi if "/" not in p and p.lower() in vendor)
    if doppie:
        problemi += 1
        print(f"❌ {len(doppie)} copie di vendor/ nella radice (filtro di cmr-renamer.spec): {', '.join(doppie[:5])}...")

    print(f"{argv[1]}: {peso_vecchio / 1e6:.1f} MB, {len(vecchio)} binari")
    print(f"{argv[2]}: {peso_nuovo / 1e6:.1f} MB, {len(nuovo)} binari "
          f"({len(set(vecchio) - set(nuovo))} tolti, {len(set(nuovo) - set(vecchio))} nuovi)")
    if problemi:
        print(f"❌ {problemi} problemi: non firmare.")
        return 1
    print("✅ Nessuna dipendenza persa: si può firmare.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
