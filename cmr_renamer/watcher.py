"""File system watcher for CMR Renamer.

Watches a directory for new PDF files and triggers OCR-based renaming.
When frozen (PyInstaller --windowed) the executable has no console by default;
if config.ini is missing a console is allocated temporarily for interactive setup,
then freed. In background mode, output is logged to a file.
"""

import os
import re
import sys
import time
import ctypes  # For Windows console manipulation
import atexit
import threading
import json
import signal
import configparser
from xml.etree import ElementTree

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageOps
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import load_or_create_config
from . import __version__, aggiornamento, gui

# Qt is optional and imported through gui.py's broad guard: without it (or
# without a display) the program runs headless — no tray, no calibrator.
if gui.QT_AVAILABLE:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPalette, QPen
    from PySide6.QtWidgets import (
        QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QMenu, QPushButton,
        QScrollArea, QSystemTrayIcon, QVBoxLayout, QWidget,
    )


# ──────────────────────────────────────────────────────────────
# Helper functions for frozen/executable detection
# ──────────────────────────────────────────────────────────────

def _is_frozen() -> bool:
    """Check if running as a PyInstaller executable."""
    return getattr(sys, 'frozen', False)


def _get_config_dir() -> str:
    """Return the directory where config.ini lives."""
    if _is_frozen():
        # Frozen (PyInstaller): config lives next to the .exe
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Normal Python: config lives in current working directory
        return os.getcwd()


def _get_resource_path(relative_path: str) -> str:
    """Risolve un percorso di risorsa bundled, sia da sorgente che da frozen (PyInstaller onefile)."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative_path)


def _get_poppler_path() -> "str | None":
    """Percorso ai binari poppler bundled quando frozen; None altrimenti (usa il PATH di sistema)."""
    if not _is_frozen():
        return None
    return _get_resource_path(os.path.join('vendor', 'poppler', 'bin'))


def _get_tesseract_cmd() -> "str | None":
    """Percorso al tesseract.exe bundled quando frozen; None altrimenti (usa il PATH di sistema)."""
    if not _is_frozen():
        return None
    return _get_resource_path(os.path.join('vendor', 'tesseract', 'bin', 'tesseract.exe'))


# ──────────────────────────────────────────────────────────────
# Console management (Windows only)
# ──────────────────────────────────────────────────────────────

_console_allocated = False

def _alloc_console():
    """Allocate a Windows console for interactive setup."""
    global _console_allocated
    if not _is_frozen() or _console_allocated:
        return # Only allocate if frozen and not already allocated

    kernel32 = ctypes.windll.kernel32
    if kernel32.AllocConsole():
        # In --windowed builds sys.stdout/stderr/stdin are None (no console
        # was attached at process start), so there is nothing to reconfigure.
        # The newly allocated console's device files must be opened and
        # assigned directly, or print()/input() will crash with
        # AttributeError as soon as they're used.
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8', errors='replace')
        sys.stderr = open('CONOUT$', 'w', encoding='utf-8', errors='replace')
        sys.stdin = open('CONIN$', 'r', encoding='utf-8', errors='replace')

        # Set console title (optional)
        kernel32.SetConsoleTitleW("CMR Renamer Setup")
        _console_allocated = True


def _free_console():
    """Free the allocated Windows console."""
    global _console_allocated
    if _console_allocated:
        for stream in (sys.stdout, sys.stderr, sys.stdin):
            try:
                stream.close()
            except Exception:
                pass
        ctypes.windll.kernel32.FreeConsole()
        _console_allocated = False


class _RotatingWriter:
    """File-like object that rotates cmr-renamer.log once it exceeds max_bytes.

    Keeps exactly one backup (`<path>.1`) instead of growing unbounded, without
    pulling in the `logging` module (this codebase uses plain `print()` with
    Italian/emoji strings everywhere; migrating every call site to `logging`
    would be an unrelated, large refactor).
    """

    def __init__(self, path: str, max_bytes: int = 1_000_000):
        self.path = path
        self.max_bytes = max_bytes
        self._file = open(path, 'a', encoding='utf-8', errors='replace')

    def write(self, data: str) -> int:
        n = self._file.write(data)
        self._file.flush()
        self._maybe_rotate()
        return n

    def flush(self):
        self._file.flush()

    def close(self):
        self._file.close()

    def _maybe_rotate(self):
        try:
            if os.path.getsize(self.path) < self.max_bytes:
                return
            self._file.close()
            backup = self.path + '.1'
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(self.path, backup)
            self._file = open(self.path, 'a', encoding='utf-8', errors='replace')
        except OSError:
            # Rotation failed (e.g. permission error) — keep appending to the
            # existing file rather than losing log output entirely.
            if self._file.closed:
                self._file = open(self.path, 'a', encoding='utf-8', errors='replace')


def _setup_file_logging(log_dir: str):
    """Redirect stdout/stderr to a size-capped, rotating log file in background mode."""
    log_path = os.path.join(log_dir, 'cmr-renamer.log')
    writer = _RotatingWriter(log_path)
    sys.stdout = writer
    sys.stderr = writer


# ──────────────────────────────────────────────────────────────
# OCR & rename helpers
# ──────────────────────────────────────────────────────────────

# Caratteri non alfanumerici mantenuti nel nome file: tutti legali su Windows
# (i vietati sono \ / : * ? " < > |). La & compare spesso nelle ragioni sociali
# ("ROSSI & FIGLI"), l'apostrofo nei nomi italiani ("L'OREAL"). Il '-' deve
# restare ultimo, altrimenti nella classe di caratteri diventa un intervallo.
_EXTRA_NAME_CHARS = r".&'-"


def _pulisci_nome(testo: str, max_len: int, rimuovi_zeri: bool) -> str:
    """Pulisce il testo OCR per usarlo come nome file."""
    clean = re.sub(rf'[^\w\s{_EXTRA_NAME_CHARS}]', '', testo).replace('\n', ' ').strip()
    if len(clean) > max_len:
        clean = clean[:max_len]
    if rimuovi_zeri:
        clean = re.sub(r'^0+', '', clean)
    return clean


RENDER_TENTATIVI = 3
RENDER_ATTESA_S = 5


def _render_con_riprova(path: str, dpi: int) -> "Image.Image | None":
    """`_render_pdf_page` ritentato se il file è ancora bloccato; None se il file non c'è più.

    Un PDF appena arrivato su una condivisione può essere ancora aperto in
    esclusiva da chi lo scrive anche dopo `_file_pronto`. Se invece il file è
    sparito (già rinominato per un evento precedente, o spostato), non è un errore.
    """
    for tentativo in range(1, RENDER_TENTATIVI + 1):
        if not os.path.exists(path):
            return None
        try:
            return _render_pdf_page(path, dpi)
        except Exception as e:
            if not os.path.exists(path):
                return None
            if tentativo == RENDER_TENTATIVI:
                raise
            print(f"⏳ '{os.path.basename(path)}' non ancora leggibile ({str(e).splitlines()[0]}): "
                  f"riprovo tra {RENDER_ATTESA_S} s ({tentativo}/{RENDER_TENTATIVI}).")
            time.sleep(RENDER_ATTESA_S)


def _render_pdf_page(path: str, dpi: int) -> "Image.Image":
    """Renderizza la pagina 1 di un PDF come immagine PIL."""
    immagini = convert_from_path(
        path, dpi=dpi, first_page=1, last_page=1,
        poppler_path=_get_poppler_path(),
    )
    return immagini[0]


# Tesseract, senza --psm esplicito, usa il proprio default 3 (analisi di layout di
# una pagina intera): sbagliato per un ritaglio di poche centinaia di pixel, ed è
# la condizione in cui la classificazione dei caratteri perde contesto e confonde
# le forme simili. 6 = "blocco uniforme di testo": regge sia una riga sola sia una
# ragione sociale su più righe (7 scarterebbe le righe dopo la prima).
OCR_PSM_DEFAULT = 6

# Bordo bianco aggiunto attorno a ogni crop prima dell'OCR: la documentazione
# ufficiale lo raccomanda per il testo tagliato stretto, che è esattamente il caso
# dei box disegnati col mouse.
OCR_BORDER_PX = 10


def _ocr_config(psm: int) -> str:
    """Stringa di configurazione da passare a pytesseract."""
    return f"--psm {int(psm)}"


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Migliora un crop prima dell'OCR: scala di grigi, contrasto, binarizzazione, bordo bianco.

    Soglia fissa (128), non derivata per immagine — punto di partenza pensato
    per tuning manuale (vedi verifica del piano), non un default definitivo.
    Il bordo si aggiunge dopo la binarizzazione, così da non falsare l'istogramma
    usato da autocontrast.
    """
    gray = img.convert('L')
    contrasted = ImageOps.autocontrast(gray)
    binaria = contrasted.point(lambda p: 255 if p > 128 else 0)
    return ImageOps.expand(binaria, border=OCR_BORDER_PX, fill=255)


# Discriminazione O/0 sulla forma del glifo. La lettera O è un cerchio quasi
# perfetto (larghezza/altezza vicino a 1), la cifra 0 è un ovale più stretto: nei
# font più diffusi, a parità di altezza, 'O' sta intorno a 1.00 e '0' intorno a
# 0.68 (Arial, Times) o 0.81 (monospazio, il caso più stretto). La soglia sta in
# mezzo, spostata un po' verso il basso perché preservare una lettera è l'errore
# meno grave. Il valore giusto dipende dal font del documento: `log_forme = true`
# in config.ini stampa il rapporto misurato di ogni glifo ambiguo per tararla,
# e `o_zero_aspect` la sovrascrive senza ricompilare.
O_ZERO_ASPECT_DEFAULT = 0.80

# Semiampiezza della fascia attorno alla soglia in cui la forma da sola non
# basta: lì decidono i caratteri vicini nella stessa parola (lettere → lettera,
# cifre → cifra), e solo se il contesto è muto resta la forma. Fuori dalla fascia
# decide sempre la forma, così uno 0 netto dentro un codice come "AB0123" resta 0.
# Su CMR reali una O di "ELEKTRONIKA" ha misurato 0.77, appena sotto 0.80.
O_ZERO_BANDA_DEFAULT = 0.10

# Margine attorno al box del carattere restituito da Tesseract entro cui cercare
# l'inchiostro del glifo, in frazione dell'altezza del box. Con il motore LSTM le
# coordinate per carattere sono approssimate e possono tagliare il glifo: una O
# vera è stata misurata 0.62, da cifra. Orizzontalmente il margine è più ampio
# perché è la larghezza a essere imprecisa.
GLIFO_MARGINE_X = 0.35
GLIFO_MARGINE_Y = 0.15
# Se l'inchiostro tocca il bordo della regione si rimisura con margini più ampi
# (multipli di quelli sopra) prima di concludere che il glifo è fuso con un vicino.
GLIFO_ALLARGAMENTI = (1, 2)

_GLIFI_O_ZERO = frozenset('Oo0')


def _elementi_classe(radice, nome: str) -> list:
    """Discendenti con `class` uguale a `nome`.

    Il match è sull'attributo class e non sul tag, così il namespace XHTML che
    Tesseract emette (`{http://www.w3.org/1999/xhtml}span`) non va gestito.
    """
    return [el for el in radice.iter() if el.get('class') == nome]


def _leggi_bbox(title: "str | None") -> "tuple[int, int, int, int] | None":
    """(x0, y0, x1, y1) da un title hOCR contenente `x_bboxes x0 y0 x1 y1` o `bbox ...`.

    Le coordinate hOCR hanno origine in ALTO a sinistra — al contrario del formato
    makebox — quindi l'altezza è y1-y0. Invertirlo capovolgerebbe il rapporto
    larghezza/altezza e con esso la discriminazione O/0.
    """
    if not title:
        return None
    for chiave in ('x_bboxes', 'bbox'):
        for campo in title.split(';'):
            parti = campo.split()
            if len(parti) >= 5 and parti[0] == chiave:
                try:
                    x0, y0, x1, y1 = (int(v) for v in parti[1:5])
                except ValueError:
                    continue
                return (x0, y0, x1, y1)
    return None


def _componenti_scure(px, larghezza: int, altezza: int, semi: tuple) -> list:
    """Componenti connesse (8-vicinato) di pixel scuri che toccano il rettangolo `semi`.

    Ritorna per ognuna {'tot', 'dentro', 'bbox': [x0, y0, x1, y1]}, dove `dentro`
    conta i pixel che cadono in `semi`. Visita in ampiezza in Python puro: la
    regione è un solo glifo con un po' di margine, poche migliaia di pixel.
    """
    sx0, sy0, sx1, sy1 = semi
    visto = bytearray(larghezza * altezza)
    componenti = []
    for y in range(sy0, sy1):
        for x in range(sx0, sx1):
            if visto[y * larghezza + x] or px[x, y] >= 128:
                continue
            visto[y * larghezza + x] = 1
            coda = [(x, y)]
            comp = {'tot': 0, 'dentro': 0, 'bbox': [x, y, x, y]}
            while coda:
                cx, cy = coda.pop()
                comp['tot'] += 1
                if sx0 <= cx < sx1 and sy0 <= cy < sy1:
                    comp['dentro'] += 1
                b = comp['bbox']
                b[0], b[1] = min(b[0], cx), min(b[1], cy)
                b[2], b[3] = max(b[2], cx), max(b[3], cy)
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        if 0 <= nx < larghezza and 0 <= ny < altezza:
                            i = ny * larghezza + nx
                            if not visto[i] and px[nx, ny] < 128:
                                visto[i] = 1
                                coda.append((nx, ny))
            componenti.append(comp)
    return componenti


def _misura_glifo(img: "Image.Image", bbox: "tuple | None") -> tuple:
    """(larghezza, altezza, ripiego) del glifo, misurate sull'inchiostro dell'immagine binarizzata.

    `ripiego` è None quando la misura viene dall'inchiostro, altrimenti dice perché
    si è tornati al box di Tesseract: con `log_forme` finisce nel log, ed è l'unico
    modo di capire sul campo se la misura sull'inchiostro sta lavorando.

    Il box per carattere di Tesseract serve solo a dire *dove* sta il glifo: la
    misura si fa sui pixel scuri attorno, cercati con un margine (`GLIFO_MARGINE_X/Y`)
    perché il box LSTM può tagliare il glifo. Si prende la componente connessa con
    più pixel dentro il box, più quelle che vi cadono per lo più dentro e hanno una
    dimensione paragonabile — i pezzi di una O spezzata dalla binarizzazione —
    escludendo così il punto di "D.O.O." e i glifi vicini.

    Se l'inchiostro arriva al bordo della regione, la regione si allarga
    (`GLIFO_ALLARGAMENTI`) e si rimisura: il box LSTM può essere semplicemente
    spostato di qualche pixel rispetto al glifo, come per l'ultima "o" di "d.o.o."
    su un CMR reale (box 13x21, inchiostro 15x16 a ridosso del bordo). Solo se
    tocca il bordo anche della regione più ampia è davvero fuso con un vicino, e
    allora — come con un inchiostro alto meno di metà box — vale il box.
    """
    if bbox is None:
        return (0, 0, "nessun box")
    x0, y0, x1, y1 = bbox
    larghezza_box, altezza_box = x1 - x0, y1 - y0
    if larghezza_box <= 0 or altezza_box <= 0:
        return (0, 0, "box vuoto")

    for fattore in GLIFO_ALLARGAMENTI:
        misura = _misura_in_regione(img, bbox, fattore)
        if misura is None:
            return (larghezza_box, altezza_box, "nessun inchiostro nel box")
        larghezza, altezza, bordi = misura
        if not bordi:
            break
    else:
        return (larghezza_box, altezza_box,
                f"inchiostro fuso oltre il margine a {'/'.join(bordi)} ({larghezza}x{altezza})")
    if altezza < altezza_box * 0.5:
        return (larghezza_box, altezza_box, f"inchiostro troppo basso ({larghezza}x{altezza})")
    return (larghezza, altezza, None)


def _misura_in_regione(img: "Image.Image", bbox: tuple, fattore: float) -> "tuple | None":
    """(larghezza, altezza, bordi toccati) dell'inchiostro attorno a `bbox`; None se non ce n'è.

    La regione è il box allargato di `fattore` volte i margini `GLIFO_MARGINE_X/Y`.
    """
    x0, y0, x1, y1 = bbox
    altezza_box = y1 - y0
    mx = max(1, round(altezza_box * GLIFO_MARGINE_X * fattore))
    my = max(1, round(altezza_box * GLIFO_MARGINE_Y * fattore))
    rx0, ry0 = max(0, x0 - mx), max(0, y0 - my)
    rx1, ry1 = min(img.width, x1 + mx), min(img.height, y1 + my)
    if rx1 <= rx0 or ry1 <= ry0:
        return None
    regione = img.crop((rx0, ry0, rx1, ry1)).convert('L')
    w, h = regione.size
    semi = (x0 - rx0, y0 - ry0, min(w, x1 - rx0), min(h, y1 - ry0))

    componenti = _componenti_scure(regione.load(), w, h, semi)
    if not componenti:
        return None

    principale = max(componenti, key=lambda c: c['dentro'])
    pw = principale['bbox'][2] - principale['bbox'][0] + 1
    ph = principale['bbox'][3] - principale['bbox'][1] + 1
    gx0, gy0, gx1, gy1 = principale['bbox']
    for c in componenti:
        if c is principale or c['dentro'] < c['tot'] * 0.5:
            continue
        cw = c['bbox'][2] - c['bbox'][0] + 1
        ch = c['bbox'][3] - c['bbox'][1] + 1
        if cw >= pw * 0.4 or ch >= ph * 0.4:
            gx0, gy0 = min(gx0, c['bbox'][0]), min(gy0, c['bbox'][1])
            gx1, gy1 = max(gx1, c['bbox'][2]), max(gy1, c['bbox'][3])

    bordi = [nome for nome, tocca in (
        ("sinistra", gx0 == 0 and rx0 > 0), ("destra", gx1 == w - 1 and rx1 < img.width),
        ("sopra", gy0 == 0 and ry0 > 0), ("sotto", gy1 == h - 1 and ry1 < img.height),
    ) if tocca]
    return (gx1 - gx0 + 1, gy1 - gy0 + 1, bordi)


def _misura_wconf(title: "str | None") -> "float | None":
    """Confidenza (0-100) da un title hOCR contenente `x_wconf N`."""
    if not title:
        return None
    for campo in title.split(';'):
        parti = campo.split()
        if len(parti) >= 2 and parti[0] == 'x_wconf':
            try:
                return float(parti[1])
            except ValueError:
                return None
    return None


def _parse_hocr(xml_bytes: bytes) -> dict:
    """Estrae testo, geometria per carattere e confidenza da un hOCR di Tesseract.

    Ritorna {'testo': str, 'glifi': [(carattere, bbox)], 'confidenze': [float]},
    con `bbox` = (x0, y0, x1, y1) nelle coordinate dell'immagine passata a
    Tesseract, oppure None. `glifi` ha un elemento per ogni carattere non-spazio
    di `testo`, nello stesso ordine: l'allineamento è per costruzione, non
    ricostruito a posteriori come servirebbe confrontando due letture indipendenti.

    Un `ocrx_word` privo di figli `ocrx_cinfo` (char box non richiesti o non
    prodotti) contribuisce comunque al testo, con bbox None che
    `_correggi_o_zero` salta.

    Solleva su XML malformato: il chiamante ripiega su `image_to_string`.
    """
    radice = ElementTree.fromstring(xml_bytes)
    righe, glifi, confidenze = [], [], []

    for riga_el in _elementi_classe(radice, 'ocr_line'):
        parole = []
        for parola_el in _elementi_classe(riga_el, 'ocrx_word'):
            conf = _misura_wconf(parola_el.get('title'))
            if conf is not None:
                confidenze.append(conf)

            cinfo = _elementi_classe(parola_el, 'ocrx_cinfo')
            if cinfo:
                testo_parola = ''
                for c in cinfo:
                    contenuto = (c.text or '').strip()
                    if not contenuto:
                        continue
                    bbox = _leggi_bbox(c.get('title'))
                    testo_parola += contenuto
                    # Il contenuto è normalmente un solo carattere; se non lo
                    # fosse, un glifo per carattere mantiene l'allineamento.
                    glifi.extend((ch, bbox) for ch in contenuto)
            else:
                testo_parola = ''.join(parola_el.itertext()).strip()
                glifi.extend((ch, None) for ch in testo_parola if not ch.isspace())

            if testo_parola:
                parole.append(testo_parola)

        if parole:
            righe.append(' '.join(parole))

    return {'testo': '\n'.join(righe), 'glifi': glifi, 'confidenze': confidenze}


def _caso_da_contesto(caratteri: list, pos: int) -> str:
    """'O' o 'o' secondo il caso prevalente delle lettere nella stessa parola."""
    minuscole = maiuscole = 0
    for direzione in (-1, 1):
        i = pos + direzione
        while 0 <= i < len(caratteri) and not caratteri[i].isspace():
            if caratteri[i].isalpha():
                if caratteri[i].islower():
                    minuscole += 1
                else:
                    maiuscole += 1
            i += direzione
    return 'o' if minuscole > maiuscole else 'O'


def _tipo_da_contesto(caratteri: list, pos: int) -> "str | None":
    """'lettera', 'cifra' o None secondo i vicini del glifo nella stessa parola.

    Per ogni lato si prende il primo carattere alfanumerico che non sia a sua
    volta O/o/0 (ambiguo per definizione), saltando la punteggiatura interna
    come i punti di "D.O.O.". Pareggio o nessun vicino: None, decide la forma.
    """
    lettere = cifre = 0
    for direzione in (-1, 1):
        i = pos + direzione
        while 0 <= i < len(caratteri) and not caratteri[i].isspace():
            c = caratteri[i]
            if c.isalnum() and c not in _GLIFI_O_ZERO:
                if c.isalpha():
                    lettere += 1
                else:
                    cifre += 1
                break
            i += direzione
    if lettere > cifre:
        return 'lettera'
    if cifre > lettere:
        return 'cifra'
    return None


def _correggi_o_zero(testo: str, glifi: list, soglia: float, log_forme: bool = False,
                     banda: float = O_ZERO_BANDA_DEFAULT) -> str:
    """Ridecide ogni glifo O/o/0 del testo in base alla forma misurata sull'immagine.

    `glifi` è una sequenza di (carattere, larghezza, altezza): gli stessi caratteri
    del testo, nello stesso ordine, ma senza spazi né ritorni a capo, con le misure
    di `_misura_glifo`. Provenendo dal medesimo parse hOCR, l'allineamento è
    garantito per costruzione; i controlli qui sotto restano come rete di sicurezza
    contro un parser modificato male, e fanno tornare il testo intatto anziché
    applicare una misura al glifo sbagliato.

    Quando il rapporto cade entro `banda` dalla soglia, la forma è considerata
    incerta e decidono i vicini (`_tipo_da_contesto`). Resta una decisione per
    singolo glifo: una cifra che è davvero una cifra e una lettera che è davvero
    una lettera restano quello che sono.
    """
    if not any(c in _GLIFI_O_ZERO for c in testo):
        return testo

    posizioni = [i for i, c in enumerate(testo) if not c.isspace()]
    if len(posizioni) != len(glifi):
        print(f"⚠️ Forme O/0 non verificabili: {len(glifi)} glifi misurati per "
              f"{len(posizioni)} caratteri riconosciuti. Testo lasciato invariato.")
        return testo

    disallineati = [
        testo[pos] for pos, (ch, _, _) in zip(posizioni, glifi)
        if testo[pos] != ch and not (testo[pos] in _GLIFI_O_ZERO and ch in _GLIFI_O_ZERO)
    ]
    if disallineati:
        print(f"⚠️ Forme O/0 non verificabili: {len(disallineati)} caratteri non "
              f"corrispondono tra le due letture. Testo lasciato invariato.")
        return testo

    caratteri = list(testo)
    for pos, (_, larghezza, altezza) in zip(posizioni, glifi):
        originale = caratteri[pos]
        if originale not in _GLIFI_O_ZERO or altezza <= 0:
            continue
        rapporto = larghezza / altezza
        tipo = 'lettera' if rapporto >= soglia else 'cifra'
        motivo = 'forma'
        if abs(rapporto - soglia) < banda:
            dal_contesto = _tipo_da_contesto(caratteri, pos)
            if dal_contesto:
                tipo, motivo = dal_contesto, 'contesto'
        if tipo == 'lettera':
            # Se Tesseract aveva già letto una lettera si conserva il suo caso,
            # altrimenti lo si deduce dalla parola.
            deciso = originale if originale.isalpha() else _caso_da_contesto(caratteri, pos)
        else:
            deciso = '0'
        if log_forme:
            print(f"   forma '{originale}' rapporto {rapporto:.2f} (soglia {soglia:.2f}) "
                  f"→ '{deciso}' [{motivo}]")
        elif deciso != originale:
            print(f"   ↻ '{originale}' → '{deciso}' (rapporto forma {rapporto:.2f}, {motivo})")
        caratteri[pos] = deciso
    return ''.join(caratteri)


# x_wconf va da 0 a 100. Sotto questa soglia il nome estratto viene comunque usato,
# con un avviso nel log: un OCR incerto è spesso ancora corretto, e bloccare la
# rinomina farebbe più danni che segnalarla.
OCR_MIN_CONFIDENCE_DEFAULT = 60.0


def _ocr_box(crop: "Image.Image", ocr_cfg: dict) -> dict:
    """OCR di un ritaglio in una sola invocazione di Tesseract.

    Ritorna {'testo': str, 'confidenza_min': float | None}.

    L'output hOCR con `hocr_char_boxes=1` porta testo, geometria per carattere e
    confidenza per parola nello stesso documento, quindi la correzione O/0 non
    richiede più una seconda invocazione: un solo processo Tesseract per ritaglio
    invece di due, e nessun riallineamento tra letture indipendenti.

    Se la chiamata hOCR o il suo parsing falliscono si ripiega su
    `image_to_string` senza correzione: un'API più ricca non deve rendere il
    programma più fragile di com'era.
    """
    psm = ocr_cfg.get('psm', OCR_PSM_DEFAULT)
    config = f"{_ocr_config(psm)} -c hocr_char_boxes=1"
    try:
        xml = pytesseract.image_to_pdf_or_hocr(
            crop, lang=ocr_cfg['lang'], config=config, extension='hocr',
        )
        risultato = _parse_hocr(xml)
    except Exception as e:
        print(f"⚠️ Lettura hOCR non riuscita ({e}): ripiego sull'OCR semplice, senza correzione O/0.")
        try:
            testo = pytesseract.image_to_string(crop, lang=ocr_cfg['lang'], config=_ocr_config(psm))
        except Exception as e2:
            print(f"❌ OCR non riuscito: {e2}")
            return {'testo': '', 'confidenza_min': None}
        return {'testo': testo, 'confidenza_min': None}

    # Si misurano solo i glifi ambigui: gli altri servono a _correggi_o_zero
    # unicamente per il controllo di allineamento.
    log_forme = ocr_cfg.get('log_forme', False)
    glifi = []
    for ch, bbox in risultato['glifi']:
        if ch not in _GLIFI_O_ZERO:
            glifi.append((ch, 0, 0))
            continue
        larghezza, altezza, ripiego = _misura_glifo(crop, bbox)
        if log_forme and bbox is not None:
            box_txt = f"box Tesseract {bbox[2] - bbox[0]}x{bbox[3] - bbox[1]}"
            print(f"   glifo '{ch}': {box_txt}, " + (
                f"inchiostro {larghezza}x{altezza}" if ripiego is None else f"uso il box: {ripiego}"))
        glifi.append((ch, larghezza, altezza))
    testo = _correggi_o_zero(
        risultato['testo'], glifi,
        ocr_cfg.get('o_zero_aspect', O_ZERO_ASPECT_DEFAULT),
        log_forme,
        ocr_cfg.get('o_zero_banda', O_ZERO_BANDA_DEFAULT),
    )
    confidenze = risultato['confidenze']
    return {
        # Il minimo, non la media: è la parola peggiore che rovina il nome file.
        'testo': testo,
        'confidenza_min': min(confidenze) if confidenze else None,
    }


# ──────────────────────────────────────────────────────────────
# Rename journal (undo support)
# ──────────────────────────────────────────────────────────────

# Timestamp ISO dell'avvio del processo, usato per raggruppare le rinomine in
# "sessioni". Tenerlo nel giornale anziché solo in memoria fa sì che l'undo
# funzioni anche dopo un riavvio dell'exe. Inizializzato in run().
_SESSIONE_ID = None


def _percorso_giornale() -> str:
    """Percorso di rinomine.log, accanto a config.ini."""
    return os.path.join(_get_config_dir(), 'rinomine.log')


def _registra_rinomina(cartella: str, originale: str, nuovo: str) -> None:
    """Aggiunge una rinomina al giornale. Mai bloccante.

    Un JSON per riga anziché TSV: dopo un OCR sfortunato un nome file può
    contenere qualunque cosa, tabulazioni incluse. Un errore di scrittura viene
    segnalato e ignorato — la rinomina è già avvenuta, e far fallire il file per
    non aver potuto annotarlo sarebbe il rimedio peggiore del male.
    """
    voce = {
        'sessione': _SESSIONE_ID,
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'cartella': cartella,
        'da': originale,
        'a': nuovo,
    }
    try:
        with open(_percorso_giornale(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(voce, ensure_ascii=False) + '\n')
    except OSError as e:
        print(f"⚠️ Impossibile aggiornare il giornale delle rinomine: {e}")


def _leggi_giornale() -> list:
    """Voci del giornale, saltando le righe illeggibili.

    Una riga troncata (crash a metà scrittura) non deve rendere inutilizzabile
    tutto lo storico.
    """
    percorso = _percorso_giornale()
    if not os.path.exists(percorso):
        return []
    voci = []
    try:
        with open(percorso, encoding='utf-8') as f:
            for riga in f:
                riga = riga.strip()
                if not riga:
                    continue
                try:
                    voci.append(json.loads(riga))
                except ValueError:
                    continue
    except OSError as e:
        print(f"⚠️ Impossibile leggere il giornale delle rinomine: {e}")
        return []
    return voci


def _rinomine_annullabili() -> list:
    """Rinomine dell'ultima sessione, se non è già stata annullata."""
    voci = _leggi_giornale()
    sessioni = [v.get('sessione') for v in voci if v.get('sessione')]
    if not sessioni:
        return []
    ultima = sessioni[-1]
    della_sessione = [v for v in voci if v.get('sessione') == ultima]
    if any(v.get('tipo') == 'undo' for v in della_sessione):
        return []
    return [v for v in della_sessione if v.get('da') and v.get('a')]


def _annulla_rinomine() -> tuple:
    """Ripristina i nomi originali dell'ultima sessione. Ritorna (ripristinati, saltati).

    Procede in ordine inverso, così una catena A→B→C torna ad A. Una voce viene
    saltata se il file col nome nuovo non c'è più o se il nome originale è già
    occupato: un undo parziale con un conteggio onesto è preferibile a uno che si
    interrompe a metà lasciando stato incoerente.
    """
    voci = _rinomine_annullabili()
    if not voci:
        return (0, 0)
    ripristinati = saltati = 0
    for v in reversed(voci):
        attuale = os.path.join(v['cartella'], v['a'])
        originale = os.path.join(v['cartella'], v['da'])
        if not os.path.exists(attuale) or os.path.exists(originale):
            saltati += 1
            continue
        try:
            os.rename(attuale, originale)
            ripristinati += 1
        except OSError as e:
            print(f"⚠️ Impossibile ripristinare '{v['a']}': {e}")
            saltati += 1
    try:
        with open(_percorso_giornale(), 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'sessione': voci[0]['sessione'], 'tipo': 'undo',
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }, ensure_ascii=False) + '\n')
    except OSError as e:
        print(f"⚠️ Impossibile marcare l'annullamento nel giornale: {e}")
    return (ripristinati, saltati)


_ANCHOR_DARK_THRESHOLD = 128  # stessa soglia di _preprocess_for_ocr
_ANCHOR_MIN_DARK_FRACTION = 0.03  # frazione minima di pixel scuri per considerare una riga/colonna "contenuto"
_ANCHOR_MIN_RUN = 4  # posizioni consecutive richieste, per ignorare rumore isolato (graffette, polvere)


def _content_profile(binaria: "Image.Image", axis_size: int, vertical: bool) -> list:
    """Frazione di pixel scuri per riga (vertical=True) o per colonna, via resize con filtro BOX."""
    if vertical:
        small = binaria.resize((1, axis_size), Image.BOX)
    else:
        small = binaria.resize((axis_size, 1), Image.BOX)
    # Image.getdata() è deprecata e verrà rimossa in Pillow 14; get_flattened_data()
    # è il sostituto ma non esiste nelle versioni più vecchie, e pyproject.toml non
    # fissa un minimo per pillow — quindi si sceglie a runtime.
    dati = small.get_flattened_data() if hasattr(small, 'get_flattened_data') else small.getdata()
    return [(255 - p) / 255 for p in dati]


def _find_content_start(profile: list) -> "int | None":
    """Primo indice con densità di scuro sufficiente, sostenuta per _ANCHOR_MIN_RUN posizioni."""
    run = 0
    for i, frac in enumerate(profile):
        if frac >= _ANCHOR_MIN_DARK_FRACTION:
            run += 1
            if run >= _ANCHOR_MIN_RUN:
                return i - _ANCHOR_MIN_RUN + 1
        else:
            run = 0
    return None


def _detect_content_anchor(img: "Image.Image") -> "tuple[int, int] | None":
    """Rileva dove inizia il contenuto (non bianco) dall'alto e da sinistra della pagina.

    Ritorna (anchor_x, anchor_y) in pixel, o None se non trova un bordo di contenuto sostenuto
    (pagina quasi bianca).
    """
    gray = img.convert('L')
    binaria = gray.point(lambda p: 0 if p < _ANCHOR_DARK_THRESHOLD else 255)
    w, h = binaria.size

    anchor_y = _find_content_start(_content_profile(binaria, h, vertical=True))
    anchor_x = _find_content_start(_content_profile(binaria, w, vertical=False))

    if anchor_x is None or anchor_y is None:
        return None
    return (anchor_x, anchor_y)


FILE_PRONTO_TIMEOUT_S = 30


def _file_pronto(path: str, timeout: int = FILE_PRONTO_TIMEOUT_S) -> bool:
    """Aspetta che il file sia finito di scrivere dalla fotocopiatrice.

    Pronto = dimensione non vuota e ferma per un secondo, *e* apribile in lettura.
    Su una condivisione SMB la sola dimensione non basta: chi scrive può tenere il
    file aperto in esclusiva anche a dimensione ferma, e Poppler fallisce allora
    con "Couldn't open file ... No error". False se il file sparisce o al timeout.
    """
    scadenza = time.monotonic() + timeout
    precedente = None
    while True:
        try:
            dimensione = os.path.getsize(path)
        except OSError:
            return False  # sparito: rinominato o spostato da qualcun altro
        if dimensione > 0 and dimensione == precedente:
            try:
                with open(path, 'rb') as f:
                    f.read(1)
                return True
            except OSError:
                pass  # ancora bloccato da chi lo scrive
        precedente = dimensione
        if time.monotonic() >= scadenza:
            return False
        time.sleep(1)


MIN_BOXES = 2
MAX_BOXES = 5

# Guards the "open calibrator + save config" critical section so the tray's
# recalibrate action and the per-file auto-calibration (on the watchdog
# observer thread) can never both open a calibrator or write
# config.ini at the same time.
_calibration_lock = threading.Lock()


def _save_calibration_to_config(boxes: list, anchor: "tuple[int, int] | None") -> None:
    """Salva le coordinate di tutti i box (2-5) e l'ancora di contenuto nel config.ini esistente."""
    config_path = os.path.join(_get_config_dir(), 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path)
    if 'OCR' not in config:
        config['OCR'] = {}
    for i, box in enumerate(boxes, start=1):
        config['OCR'][f'box{i}'] = ','.join(str(int(v)) for v in box)
    # Rimuove eventuali chiavi box(N+1).. rimaste da una configurazione precedente
    # con più box (es. da 4 box a 3: box4 va eliminato, non lasciato stantio).
    # box{N}_chars veniva scritta da una versione intermedia mai rilasciata: si
    # rimuove sempre, così un config.ini che l'avesse non resta con chiavi morte.
    for i in range(1, MAX_BOXES + 1):
        if i > len(boxes):
            config['OCR'].pop(f'box{i}', None)
        config['OCR'].pop(f'box{i}_chars', None)
    if anchor is not None:
        config['OCR']['anchor_x'] = str(int(anchor[0]))
        config['OCR']['anchor_y'] = str(int(anchor[1]))
    else:
        config['OCR'].pop('anchor_x', None)
        config['OCR'].pop('anchor_y', None)
    with open(config_path, 'w') as f:
        config.write(f)


def _load_boxes_from_config(ocr_section) -> list:
    """Legge box1..box5 da una sezione [OCR] già caricata, in ordine.

    Si ferma al primo box assente — sufficiente perché _save_calibration_to_config
    scrive sempre chiavi contigue a partire da box1.
    """
    boxes = []
    for i in range(1, MAX_BOXES + 1):
        raw = ocr_section.get(f'box{i}')
        if not raw:
            break
        boxes.append(tuple(map(int, raw.split(','))))
    return boxes


def _load_anchor_from_config(ocr_section) -> "tuple[int, int] | None":
    """Legge anchor_x/anchor_y da una sezione [OCR] già caricata, se entrambi presenti."""
    x = ocr_section.get('anchor_x')
    y = ocr_section.get('anchor_y')
    if x is None or y is None:
        return None
    return (int(x), int(y))


BOX_COLORS = ["red", "blue", "green", "orange", "purple"]


def _default_box(index: int) -> tuple:
    """Rettangolo di default per un nuovo box, offsettato per non sovrapporsi agli altri."""
    offset = 20 * index
    return (20 + offset, 20 + offset, 220 + offset, 120 + offset)


def _box_label(index: int) -> str:
    """Etichetta leggibile per il box all'indice 0-based `index`."""
    semantic = {0: "numero documento", 1: "ragione sociale"}
    if index in semantic:
        return f"box {index + 1} ({semantic[index]})"
    return f"box {index + 1}"


def _list_watched_pdfs(folder: str) -> list:
    """Elenca i percorsi assoluti dei PDF nella cartella, ordinati per nome file."""
    if not os.path.isdir(folder):
        return []
    nomi = sorted(f for f in os.listdir(folder) if f.endswith('.pdf'))
    return [os.path.join(folder, f) for f in nomi]


def _calibra_box(pdf_paths: list, initial_path: str, boxes: list, ocr_cfg: dict, name_cfg: dict):
    """Mostra la pagina 1 di un PDF a scelta tra `pdf_paths` e permette di ridisegnare 2-5 box col mouse.

    `pdf_paths` è l'elenco dei PDF della cartella monitorata, selezionabili da una lista laterale
    per confrontare visivamente se i box calibrati si applicano bene a più documenti; il cambio file
    ridisegna solo l'immagine di sfondo e sposta i box del solo scostamento di deriva rilevato.
    `initial_path` è il file mostrato all'apertura (preselezionato in lista). `boxes` è una lista di
    partenza di 2-5 tuple (x1,y1,x2,y2). `ocr_cfg`/`name_cfg` servono al pulsante "Prova OCR", che
    esegue la stessa catena di `_rinomina_pdf` sui box correnti; il dpi di rendering viene da
    `ocr_cfg['dpi']`. Se l'utente salva, ritorna
    {'boxes': [...], 'anchor': (x,y) | None} — l'ancora è rilevata
    sull'immagine visualizzata al momento del salvataggio (non necessariamente quella di
    `initial_path`, se nel frattempo si è passati a un altro file dalla lista). Ritorna None se
    l'utente annulla.

    Callable from any thread: the page is rendered here, the window itself runs
    on the GUI thread through `gui.esegui_in_gui`.
    """
    if not gui.disponibile():
        print("⚠️ Interfaccia grafica non disponibile: calibrazione box saltata.")
        return None
    try:
        # Rendering takes seconds on a network share: done on the calling
        # thread so the GUI stays responsive while it happens.
        img = _render_pdf_page(initial_path, ocr_cfg['dpi'])
        return gui.esegui_in_gui(_finestra_calibrazione, pdf_paths, initial_path, img,
                                 boxes, ocr_cfg, name_cfg)
    except Exception as e:
        print(f"⚠️ Errore durante la calibrazione dei box: {e}")
        return None


if gui.QT_AVAILABLE:
    class _PaginaCalibrazione(QWidget):
        """The rendered page with the boxes on top; drag to redraw the active box.

        Pure view: geometry lives in the calibrator's `state`, and mouse
        results are handed back through `on_rilascio(x0, y0, x1, y1)` in
        widget (= scaled image) coordinates.
        """

        def __init__(self, state: dict, on_rilascio, on_rotella):
            super().__init__()
            self.state = state
            self.on_rilascio = on_rilascio
            self.on_rotella = on_rotella
            self.pixmap = None
            self.scala = 1.0
            self.inizio = None
            self.corrente = None
            self.setCursor(Qt.CrossCursor)

        def imposta(self, pixmap, scala: float):
            self.pixmap = pixmap
            self.scala = scala
            self.setFixedSize(pixmap.size())
            self.update()

        def paintEvent(self, event):
            if self.pixmap is None:
                return
            p = QPainter(self)
            p.drawPixmap(0, 0, self.pixmap)
            dx, dy = self.state['preview_shift']
            s = self.scala
            for i, (x1, y1, x2, y2) in enumerate(self.state['boxes']):
                p.setPen(QPen(QColor(BOX_COLORS[i]), 3))
                p.drawRect(QRectF((x1 + dx) * s, (y1 + dy) * s, (x2 - x1) * s, (y2 - y1) * s))
            if self.inizio is not None and self.corrente is not None:
                penna = QPen(QColor(BOX_COLORS[self.state['active']]), 2, Qt.DashLine)
                p.setPen(penna)
                p.drawRect(QRectF(self.inizio, self.corrente).normalized())
            p.end()

        def mousePressEvent(self, event):
            if event.button() == Qt.LeftButton:
                self.inizio = event.position()
                self.corrente = self.inizio

        def mouseMoveEvent(self, event):
            if self.inizio is not None:
                self.corrente = event.position()
                self.update()

        def mouseReleaseEvent(self, event):
            if event.button() != Qt.LeftButton or self.inizio is None:
                return
            a, b = self.inizio, event.position()
            self.inizio = self.corrente = None
            self.update()
            self.on_rilascio(a.x(), a.y(), b.x(), b.y())

        def wheelEvent(self, event):
            # The wheel zooms, like the +/− buttons, instead of scrolling.
            self.on_rotella(event.angleDelta().y())
            event.accept()


def _finestra_calibrazione(pdf_paths: list, initial_path: str, initial_img: "Image.Image",
                           boxes: list, ocr_cfg: dict, name_cfg: dict):
    """The calibrator window proper; GUI thread only. See `_calibra_box`."""
    dpi = ocr_cfg['dpi']

    MIN_DRAG = 4  # px — ignore accidental clicks/near-zero drags
    MAX_ZOOM = 6.0  # relative to the initial fit-to-screen view
    MAX_DIM = 8000  # px safety cap on the rendered (zoomed) image size

    image_cache: dict = {initial_path: initial_img}

    def get_image(path: str) -> "Image.Image":
        if path not in image_cache:
            image_cache[path] = _render_pdf_page(path, dpi)
        return image_cache[path]

    state = {
        'boxes': list(boxes),
        'active': 0, 'result': None,
        'zoom': 1.0, 'img': initial_img, 'current_path': initial_path,
        'reference_anchor': _detect_content_anchor(initial_img), 'preview_shift': (0, 0),
        'pixmap_full': gui.da_pil(initial_img),
    }
    # initial_path IS the reference for this session, so its own shift is always (0, 0);
    # update_preview_shift() only needs to run again once a *different* file is selected.

    def update_preview_shift():
        current = _detect_content_anchor(state['img'])
        state['preview_shift'] = _compute_anchor_shift(current, state['reference_anchor'], dpi)

    dialogo = QDialog()
    dialogo.setWindowTitle("CMR Renamer - Calibrazione box OCR")
    dialogo.setWindowFlag(Qt.WindowMaximizeButtonHint)
    layout = QVBoxLayout(dialogo)

    schermo = dialogo.screen().availableGeometry()
    screen_w = max(schermo.width() - 380, 300)
    screen_h = max(schermo.height() - 260, 300)
    base_scale = min(screen_w / initial_img.width, screen_h / initial_img.height, 1.0)
    max_zoom = min(
        MAX_ZOOM,
        MAX_DIM / (initial_img.width * base_scale),
        MAX_DIM / (initial_img.height * base_scale),
    )

    # ── Top row: box selectors, box count, zoom ──
    top = QHBoxLayout()
    layout.addLayout(top)
    select_row = QHBoxLayout()
    top.addLayout(select_row)
    top.addSpacing(20)
    add_btn = QPushButton("+ Box")
    remove_btn = QPushButton("− Box")
    top.addWidget(add_btn)
    top.addWidget(remove_btn)
    top.addSpacing(20)
    zoom_out_btn = QPushButton("−")
    zoom_label = QLabel("100%")
    zoom_label.setMinimumWidth(48)
    zoom_label.setAlignment(Qt.AlignCenter)
    zoom_in_btn = QPushButton("+")
    zoom_reset_btn = QPushButton("Reset zoom")
    for w in (zoom_out_btn, zoom_in_btn):
        w.setFixedWidth(32)
    for w in (zoom_out_btn, zoom_label, zoom_in_btn, zoom_reset_btn):
        top.addWidget(w)
    top.addStretch(1)

    label = QLabel("")
    layout.addWidget(label)

    # ── Body: file list + page ──
    body = QHBoxLayout()
    layout.addLayout(body, 1)
    sidebar = QVBoxLayout()
    body.addLayout(sidebar)
    sidebar.addWidget(QLabel("File nella cartella:"))
    file_list = QListWidget()
    file_list.setFixedWidth(260)
    for path in pdf_paths:
        file_list.addItem(os.path.basename(path))
    sidebar.addWidget(file_list, 1)

    def on_rilascio(x0, y0, x1, y1):
        if abs(x1 - x0) < MIN_DRAG or abs(y1 - y0) < MIN_DRAG:
            return  # too small to be an intentional box — ignore
        scale = total_scale()
        disp_w = max(int(state['img'].width * scale), 1)
        disp_h = max(int(state['img'].height * scale), 1)
        x0 = min(max(x0, 0), disp_w)
        x1 = min(max(x1, 0), disp_w)
        y0 = min(max(y0, 0), disp_h)
        y1 = min(max(y1, 0), disp_h)
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        dx, dy = state['preview_shift']
        state['boxes'][state['active']] = (
            int(x0 / scale) - dx, int(y0 / scale) - dy,
            int(x1 / scale) - dx, int(y1 / scale) - dy,
        )
        pagina.update()

    def on_rotella(delta):
        if delta > 0:
            zoom_in()
        elif delta < 0:
            zoom_out()

    pagina = _PaginaCalibrazione(state, on_rilascio, on_rotella)
    scroll = QScrollArea()
    scroll.setWidget(pagina)
    scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    # Palette, not a stylesheet: a stylesheet would also strip the native
    # look from the scroll bars.
    sfondo = scroll.viewport().palette()
    sfondo.setColor(QPalette.Window, QColor("#333333"))
    scroll.viewport().setPalette(sfondo)
    scroll.viewport().setAutoFillBackground(True)
    scroll.setMinimumSize(
        min(int(initial_img.width * base_scale) + 4, screen_w),
        min(int(initial_img.height * base_scale) + 4, screen_h),
    )
    body.addWidget(scroll, 1)

    # ── Bottom: OCR test result and buttons ──
    prova_label = QLabel("")
    prova_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(prova_label)
    bottoni = QHBoxLayout()
    layout.addLayout(bottoni)
    bottoni.addStretch(1)
    prova_btn = QPushButton("Prova OCR")
    salva_btn = QPushButton("Salva")
    annulla_btn = QPushButton("Annulla")
    for w in (prova_btn, salva_btn, annulla_btn):
        bottoni.addWidget(w)
    bottoni.addStretch(1)

    select_buttons: dict = {}

    def total_scale():
        return base_scale * state['zoom']

    def render():
        scale = total_scale()
        disp_w = max(int(state['img'].width * scale), 1)
        disp_h = max(int(state['img'].height * scale), 1)
        pixmap = state['pixmap_full'].scaled(disp_w, disp_h, Qt.IgnoreAspectRatio,
                                             Qt.SmoothTransformation)
        pagina.imposta(pixmap, scale)
        zoom_label.setText(f"{int(state['zoom'] * 100)}%")

    def set_zoom(new_zoom):
        new_zoom = max(1.0, min(new_zoom, max_zoom))
        if new_zoom == state['zoom']:
            return
        state['zoom'] = new_zoom
        render()

    def zoom_in():
        set_zoom(state['zoom'] * 1.25)

    def zoom_out():
        set_zoom(state['zoom'] / 1.25)

    zoom_out_btn.clicked.connect(zoom_out)
    zoom_in_btn.clicked.connect(zoom_in)
    zoom_reset_btn.clicked.connect(lambda: set_zoom(1.0))

    def set_active(index):
        state['active'] = index
        for i, btn in select_buttons.items():
            btn.setChecked(i == index)
        label.setText(f"Box attivo: {_box_label(index)}. Trascina col mouse per ridisegnarlo "
                      f"(rotellina per zoomare).")

    def update_count_buttons():
        add_btn.setEnabled(len(state['boxes']) < MAX_BOXES)
        remove_btn.setEnabled(len(state['boxes']) > MIN_BOXES)

    def rebuild_select_buttons():
        for btn in select_buttons.values():
            select_row.removeWidget(btn)
            # deleteLater() acts on the next loop iteration: until then an
            # unmanaged, still-visible button would float over the window.
            btn.hide()
            btn.deleteLater()
        select_buttons.clear()
        for i in range(len(state['boxes'])):
            # Native button: the swatch says which rectangle it is, the
            # pressed (checked) state which one the next drag redraws.
            btn = QPushButton(gui.campione_colore(BOX_COLORS[i]), _box_label(i))
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, n=i: set_active(n))
            select_row.addWidget(btn)
            select_buttons[i] = btn
        update_count_buttons()

    def add_box():
        if len(state['boxes']) >= MAX_BOXES:
            return
        state['boxes'].append(_default_box(len(state['boxes'])))
        rebuild_select_buttons()
        set_active(len(state['boxes']) - 1)
        pagina.update()

    def remove_box():
        if len(state['boxes']) <= MIN_BOXES:
            return
        removed = state['active']
        del state['boxes'][removed]
        rebuild_select_buttons()
        set_active(max(removed - 1, 0))
        pagina.update()

    add_btn.clicked.connect(add_box)
    remove_btn.clicked.connect(remove_box)

    def on_file_select(row):
        if row < 0:
            return
        path = pdf_paths[row]
        if path == state['current_path']:
            return
        try:
            new_img = get_image(path)
        except Exception as e:
            print(f"⚠️ Impossibile aprire '{os.path.basename(path)}': {e}")
            file_list.blockSignals(True)
            file_list.setCurrentRow(pdf_paths.index(state['current_path']))
            file_list.blockSignals(False)
            return
        state['img'] = new_img
        state['pixmap_full'] = gui.da_pil(new_img)
        state['current_path'] = path
        update_preview_shift()
        render()

    if initial_path in pdf_paths:
        file_list.setCurrentRow(pdf_paths.index(initial_path))
    file_list.currentRowChanged.connect(on_file_select)

    def on_save():
        state['result'] = {
            'boxes': list(state['boxes']),
            'anchor': _detect_content_anchor(state['img']),
        }
        dialogo.accept()

    def on_prova():
        """Esegue l'OCR sui box correnti e mostra cosa produrrebbero.

        Usa lo stesso `preview_shift` applicato al disegno, così misura
        esattamente i ritagli che il programma userebbe su questo file, e la
        stessa catena `_ocr_box` → `_pulisci_nome` → join di `_rinomina_pdf`.
        """
        prova_label.setStyleSheet("color: #444444;")
        prova_label.setText("Prova in corso...")
        # L'OCR è sincrono e blocca la finestra: si forza il ridisegno prima.
        QApplication.processEvents()
        dx, dy = state['preview_shift']
        righe, parti = [], []
        try:
            for i, (x1, y1, x2, y2) in enumerate(state['boxes'], start=1):
                crop = _preprocess_for_ocr(
                    state['img'].crop((x1 + dx, y1 + dy, x2 + dx, y2 + dy))
                )
                letto = _ocr_box(crop, ocr_cfg)
                pulito = _pulisci_nome(
                    letto['testo'], name_cfg['max_length'], name_cfg['remove_leading_zeros'],
                )
                if pulito:
                    parti.append(pulito)
                conf = letto['confidenza_min']
                conf_txt = "n/d" if conf is None else f"{conf:.0f}"
                righe.append(f"Box {i}: '{pulito}'   (confidenza {conf_txt})")
        except Exception as e:
            prova_label.setStyleSheet("color: #880000;")
            prova_label.setText(f"Prova non riuscita: {e}")
            return
        nome = " ".join(parti).strip() or "documento_senza_nome"
        righe.append(f"→ nome file: '{nome}.pdf'")
        prova_label.setStyleSheet("color: #004400;")
        prova_label.setText("\n".join(righe))

    prova_btn.clicked.connect(on_prova)
    salva_btn.clicked.connect(on_save)
    annulla_btn.clicked.connect(dialogo.reject)

    rebuild_select_buttons()
    set_active(0)
    render()

    # Opened from the tray or a background thread: make sure it is not left
    # behind other windows.
    dialogo.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    dialogo.show()
    dialogo.raise_()
    dialogo.activateWindow()
    dialogo.setWindowFlag(Qt.WindowStaysOnTopHint, False)
    dialogo.show()
    dialogo.exec()
    return state['result']


def _build_tray_icon(icon_image: "Image.Image", ocr_cfg: dict, name_cfg: dict, log_path: str,
                      cartella: str, stop_event: "threading.Event",
                      aggiornatore: "aggiornamento.Aggiornatore | None" = None) -> "QSystemTrayIcon":
    """Crea l'icona di system tray con il menu di controllo del background mode.

    GUI thread only. The actions run there too, so anything slow (network,
    renames on the share) is moved to a thread; message boxes go back through
    `gui`, which works from any thread.
    """

    def _controlla_aggiornamenti():
        # Rete e download possono durare decine di secondi: fuori dal thread
        # della GUI, che altrimenti resterebbe bloccato.
        def controlla():
            esito = aggiornatore.controlla()
            if "riavvio in corso" not in esito:
                try:
                    gui.mostra_info("CMR Renamer", esito)
                except Exception as e:
                    print(f"⚠️ Impossibile mostrare l'esito ({e}).")

        threading.Thread(target=controlla, daemon=True).start()

    def _open_log():
        try:
            os.startfile(os.path.normpath(log_path))
        except Exception as e:
            print(f"⚠️ Impossibile aprire il log: {e}")

    def _open_folder():
        try:
            os.startfile(os.path.normpath(cartella))
        except Exception as e:
            print(f"⚠️ Impossibile aprire la cartella: {e}")

    def _recalibra():
        if not _calibration_lock.acquire(blocking=False):
            print("⚠️ Calibrazione già in corso altrove: riprova più tardi.")
            return
        try:
            pdf_paths = _list_watched_pdfs(cartella)
            if not pdf_paths:
                print("⚠️ Nessun PDF trovato nella cartella monitorata.")
                return
            boxes_seed = list(ocr_cfg['boxes'])
            while len(boxes_seed) < MIN_BOXES:
                boxes_seed.append(_default_box(len(boxes_seed)))
            risultato = _calibra_box(pdf_paths, pdf_paths[0], boxes_seed, ocr_cfg, name_cfg)
            if risultato:
                ocr_cfg['boxes'] = risultato['boxes']
                ocr_cfg['anchor'] = risultato['anchor']
                _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
        except Exception as e:
            print(f"⚠️ Errore durante la ricalibrazione: {e}")
        finally:
            _calibration_lock.release()

    def _annulla():
        voci = _rinomine_annullabili()
        if not voci:
            print("⚠️ Nessuna rinomina da annullare in questa sessione.")
            return
        # Tocca file su una condivisione di rete e una voce di menu è troppo
        # facile da centrare per sbaglio: si chiede conferma quando è possibile.
        try:
            conferma = gui.chiedi_conferma(
                "CMR Renamer",
                f"Ripristinare i nomi originali di {len(voci)} file rinominati "
                f"in questa sessione?",
            )
        except Exception as e:
            print(f"⚠️ Impossibile mostrare la conferma ({e}): procedo comunque.")
            conferma = None
        if conferma is False:
            print("↩️ Annullamento non confermato: nessuna modifica.")
            return

        def annulla():
            ripristinati, saltati = _annulla_rinomine()
            print(f"↩️ Annullamento completato: {ripristinati} ripristinati, {saltati} saltati.")

        threading.Thread(target=annulla, daemon=True).start()

    def _exit():
        # The event loop notices within 200 ms (gui.ciclo_eventi) and run()
        # shuts the observer down in order.
        stop_event.set()

    tray = QSystemTrayIcon(gui.icona(icon_image))
    tray.setToolTip("CMR Renamer")
    menu = QMenu()
    versione = menu.addAction(f"CMR Renamer {__version__}")
    versione.setEnabled(False)
    menu.addSeparator()
    menu.addAction("Apri log").triggered.connect(_open_log)
    menu.addAction("Apri cartella monitorata").triggered.connect(_open_folder)
    menu.addAction("Ricalibra box").triggered.connect(_recalibra)
    menu.addAction("Annulla ultime rinomine").triggered.connect(_annulla)
    if aggiornatore is not None:
        menu.addAction("Controlla aggiornamenti").triggered.connect(_controlla_aggiornamenti)
    menu.addSeparator()
    menu.addAction("Esci").triggered.connect(_exit)
    tray.setContextMenu(menu)
    # setContextMenu does not take ownership: without this reference Python
    # would collect the menu and right-clicking the icon would show nothing.
    tray._menu = menu
    return tray


MAX_ANCHOR_SHIFT_MM = 15  # oltre questa soglia lo spostamento rilevato è considerato inaffidabile


def _compute_anchor_shift(current: "tuple[int, int] | None", reference: "tuple[int, int] | None", dpi: int) -> "tuple[int, int]":
    """Calcola lo spostamento (dx, dy) tra due ancore di contenuto.

    Ritorna (0, 0) se manca un'ancora, o se lo spostamento supera la soglia plausibile
    (MAX_ANCHOR_SHIFT_MM convertita in pixel in base al dpi).
    """
    if reference is None or current is None:
        return (0, 0)
    dx = current[0] - reference[0]
    dy = current[1] - reference[1]
    max_shift_px = dpi * MAX_ANCHOR_SHIFT_MM / 25.4
    if abs(dx) > max_shift_px or abs(dy) > max_shift_px:
        return (0, 0)
    return (dx, dy)


def _resolve_crop_boxes(img: "Image.Image", ocr_cfg: dict) -> list:
    """Ritorna i box da ritagliare, corretti per la deriva di scansione quando possibile.

    Se non c'è un'ancora di riferimento salvata, se l'ancora non è rilevabile sulla pagina
    corrente, o se lo spostamento rilevato supera la soglia plausibile, ritorna i box calibrati
    senza modifiche — il file viene comunque elaborato, solo senza correzione.
    """
    boxes = ocr_cfg['boxes']
    reference = ocr_cfg.get('anchor')
    if reference is None:
        return boxes

    current = _detect_content_anchor(img)
    if current is None:
        print("⚠️ Ancora di contenuto non rilevabile: uso i box calibrati senza correzione deriva.")
        return boxes

    raw_dx = current[0] - reference[0]
    raw_dy = current[1] - reference[1]
    dx, dy = _compute_anchor_shift(current, reference, ocr_cfg['dpi'])
    if (dx, dy) != (raw_dx, raw_dy):
        print(f"⚠️ Spostamento rilevato ({raw_dx}, {raw_dy}px) oltre la soglia plausibile: uso i box calibrati senza correzione deriva.")
        return boxes

    return [(x1 + dx, y1 + dy, x2 + dx, y2 + dy) for (x1, y1, x2, y2) in boxes]


def _salva_ritaglio_debug(crop: "Image.Image", pdf_path: str, indice: int) -> None:
    """Con `log_forme`, salva il ritaglio esattamente come lo riceve Tesseract.

    In `debug_ocr/` accanto a config.ini, un PNG per box e per file: è ciò che serve
    per capire un errore di lettura senza avere accesso alle scansioni originali.
    """
    try:
        cartella = os.path.join(_get_config_dir(), 'debug_ocr')
        os.makedirs(cartella, exist_ok=True)
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        percorso = os.path.join(cartella, f"{base}_box{indice}.png")
        crop.save(percorso)
        print(f"   🖼️ ritaglio box {indice} salvato in {percorso}")
    except Exception as e:
        print(f"⚠️ Impossibile salvare il ritaglio di debug: {e}")


def _rinomina_pdf(pdf_path: str, ocr_cfg: dict, name_cfg: dict) -> None:
    """Esegue OCR e rinomina il PDF con il testo estratto."""
    try:
        img = _render_con_riprova(pdf_path, ocr_cfg['dpi'])
        if img is None:
            print(f"ℹ️ '{os.path.basename(pdf_path)}' non c'è più (già rinominato o spostato): niente da fare.")
            return

        serve_calibrazione = len(ocr_cfg['boxes']) < MIN_BOXES
        if ocr_cfg['show_rects'] or serve_calibrazione:
            if not _calibration_lock.acquire(blocking=False):
                print("⚠️ Calibrazione già in corso altrove (es. dal tray): salto per questo file.")
                if serve_calibrazione:
                    return
            else:
                try:
                    if serve_calibrazione:
                        print("🖱️ Box OCR non ancora configurati: selezionali con il mouse.")
                    boxes_seed = list(ocr_cfg['boxes'])
                    while len(boxes_seed) < MIN_BOXES:
                        boxes_seed.append(_default_box(len(boxes_seed)))
                    pdf_paths = _list_watched_pdfs(os.path.dirname(pdf_path))
                    risultato = _calibra_box(pdf_paths, pdf_path, boxes_seed, ocr_cfg, name_cfg)
                    if risultato:
                        ocr_cfg['boxes'] = risultato['boxes']
                        ocr_cfg['anchor'] = risultato['anchor']
                        _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'])
                        print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
                    elif serve_calibrazione:
                        print(f"⚠️ Calibrazione annullata: '{os.path.basename(pdf_path)}' non elaborato (nessun box configurato).")
                        return
                finally:
                    _calibration_lock.release()

        parti = []
        soglia_conf = ocr_cfg.get('min_confidence', OCR_MIN_CONFIDENCE_DEFAULT)
        for i, box in enumerate(_resolve_crop_boxes(img, ocr_cfg), start=1):
            crop = _preprocess_for_ocr(img.crop(box))
            if ocr_cfg.get('log_forme'):
                _salva_ritaglio_debug(crop, pdf_path, i)
            letto = _ocr_box(crop, ocr_cfg)
            conf = letto['confidenza_min']
            if conf is not None and conf < soglia_conf:
                print(f"⚠️ Box {i}: confidenza OCR bassa ({conf:.0f} < {soglia_conf:.0f}). "
                      f"Rinomino comunque, ma il nome potrebbe essere errato.")
            # _pulisci_nome va dopo la correzione O/0 (già fatta in _ocr_box),
            # perché tronca a max_length e rimuove gli zeri iniziali.
            pulito = _pulisci_nome(letto['testo'], name_cfg['max_length'], name_cfg['remove_leading_zeros'])
            if pulito:
                parti.append(pulito)

        base = " ".join(parti).strip()
        if not base:
            base = "documento_senza_nome"

        dest_dir = os.path.dirname(pdf_path)
        nuovo_path = os.path.join(dest_dir, f"{base}.pdf")
        nome_originale = os.path.basename(pdf_path)

        if not os.path.exists(nuovo_path):
            os.rename(pdf_path, nuovo_path)
            print(f"✅ Rinominato → '{os.path.basename(nuovo_path)}'")
        else:
            counter = 1
            while os.path.exists(nuovo_path):
                nuovo_path = os.path.join(dest_dir, f"{base} ({counter}).pdf")
                counter += 1
            os.rename(pdf_path, nuovo_path)
            print(f"✅ Rinominato → '{os.path.basename(nuovo_path)}' (conflitto risolto)")
        _registra_rinomina(dest_dir, nome_originale, os.path.basename(nuovo_path))

    except Exception as e:
        print(f"❌ Errore per '{os.path.basename(pdf_path)}': {e}")


# ──────────────────────────────────────────────────────────────
# File system watcher
# ──────────────────────────────────────────────────────────────

class CMRHandler(FileSystemEventHandler):
    """Event handler che processa i PDF appena creati/spostati/modificati."""

    def __init__(self, ocr_cfg: dict, name_cfg: dict, delay: int, prefix: str):
        super().__init__()
        self.processati: dict[str, float] = {}
        self.ocr_cfg = ocr_cfg
        self.name_cfg = name_cfg
        self.delay = delay
        self.prefix = prefix

    def on_created(self, event):
        if not event.is_directory:
            self._processa(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._processa(event.dest_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._processa(event.src_path)

    def _processa(self, path: str) -> None:
        if not path or not path.endswith('.pdf'):
            return
        if not os.path.basename(path).startswith(self.prefix):
            return

        now = time.time()
        if path in self.processati and now - self.processati[path] < self.delay:
            return
        self.processati[path] = now

        if not os.path.exists(path):
            # Evento arrivato in coda dopo che il file era già stato rinominato.
            return
        print(f"⏳ Attendo: {os.path.basename(path)}")
        if not _file_pronto(path):
            if not os.path.exists(path):
                return
            print(f"⚠️ Timeout attesa: {os.path.basename(path)} — provo comunque.")
        print(f"✔ Elaboro: {path}")
        _rinomina_pdf(path, self.ocr_cfg, self.name_cfg)


# ──────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────

def run() -> int:
    """Main entry point: load config, process existing files, start watcher.

    Returns 0 on success.
    """
    global _SESSIONE_ID
    # Marca tutte le rinomine di questa esecuzione, così "Annulla ultime rinomine"
    # sa cosa ripristinare anche dopo un riavvio dell'exe.
    _SESSIONE_ID = time.strftime('%Y-%m-%dT%H:%M:%S')

    config_dir = _get_config_dir()
    config_path = os.path.join(config_dir, 'config.ini')
    config_exists = os.path.exists(config_path)

    # ── Background vs interactive mode ─────────────────────
    if _is_frozen() and not config_exists and not gui.puo_avviarsi():
        # Frozen + no config + no Qt → allocate a console so the text
        # prompts are visible, since there's no GUI form to fall back to.
        _alloc_console()
        print("\n=== CMR Renamer - Configurazione Iniziale ===\n")
        try:
            cfg = load_or_create_config(config_path=config_path)
        except Exception as e:
            print(f"❌ Errore durante la configurazione: {e}")
            _free_console()
            return 1 # Indicate error
        else:
            # No exception
            _free_console()
            # After setup, continue in background mode
            _setup_file_logging(config_dir)
    elif _is_frozen() and not config_exists:
        # Frozen + no config + Qt available → the GUI form needs no console.
        # File logging must be set up BEFORE load_or_create_config runs: a
        # --windowed build with no console allocated has sys.stdout/stderr set to
        # None, and load_or_create_config prints status messages — those would
        # raise AttributeError without somewhere to write to first.
        _setup_file_logging(config_dir)
        try:
            cfg = load_or_create_config(config_path=config_path)
        except Exception as e:
            print(f"❌ Errore durante la configurazione: {e}")
            return 1 # Indicate error
    elif _is_frozen() and config_exists:
        # Frozen + config exists → background mode (no console)
        _setup_file_logging(config_dir)
        cfg = load_or_create_config(config_path=config_path)
    else:
        # Normal Python execution → use console as-is
        cfg = load_or_create_config(config_path=config_path)

    # ── Bundled native tools (frozen only) ──────────────────
    if _is_frozen():
        tess_cmd = _get_tesseract_cmd()
        if tess_cmd:
            pytesseract.pytesseract.tesseract_cmd = tess_cmd
            os.environ['TESSDATA_PREFIX'] = _get_resource_path(os.path.join('vendor', 'tesseract', 'tessdata'))

    # ── Parse config ───────────────────────────────────────
    try:
        cartella = cfg['Watcher']['folder']
        delay_riavvio = int(cfg['Watcher']['delay_riavvio'])
    except KeyError as e:
        print(f"❌ Errore di configurazione: mancante chiave {e}")
        return 1
    # 'prefix' is missing from config.ini files created before this option
    # existed — fall back to the original hardcoded behavior.
    prefix = cfg['Watcher'].get('prefix', 'DOC')

    # box1..box5 are absent from freshly-created config.ini files (the box
    # count and coordinates are selected with the mouse on the first PDF
    # processed, not prompted for at setup time); show_rects likewise has no
    # setup prompt and only takes effect if a user hand-edits config.ini.
    # psm, o_zero_aspect and log_forme have no prompt either: they exist so the
    # segmentation mode and the O/0 shape threshold can be tuned by hand, and the
    # measured ratios logged for that tuning, without a rebuild.
    boxes = _load_boxes_from_config(cfg['OCR'])
    ocr_cfg = {
        'boxes': boxes,
        'anchor': _load_anchor_from_config(cfg['OCR']),
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
        'psm': cfg['OCR'].getint('psm', fallback=OCR_PSM_DEFAULT),
        'o_zero_aspect': cfg['OCR'].getfloat('o_zero_aspect', fallback=O_ZERO_ASPECT_DEFAULT),
        'o_zero_banda': cfg['OCR'].getfloat('o_zero_banda', fallback=O_ZERO_BANDA_DEFAULT),
        'min_confidence': cfg['OCR'].getfloat('min_confidence', fallback=OCR_MIN_CONFIDENCE_DEFAULT),
        'log_forme': cfg['OCR'].getboolean('log_forme', fallback=False),
    }

    name_cfg = {
        'max_length': int(cfg['Filename']['max_length']),
        'remove_leading_zeros': cfg['Filename'].getboolean('remove_leading_zeros'),
    }

    # [Aggiornamenti] è opzionale e senza prompt, come show_rects: il token del
    # repository privato si aggiunge a mano. Senza token non si controlla nulla.
    token_aggiornamenti = cfg.get('Aggiornamenti', 'token', fallback='').strip()
    aggiornamenti_attivi = cfg.getboolean('Aggiornamenti', 'attivo', fallback=True)
    print(f"ℹ️ CMR Renamer {__version__} — lingue OCR: {ocr_cfg['lang']}")
    if _is_frozen():
        aggiornamento.pulisci_residui(sys.executable)

    # Qt widgets live on the main thread, so from here on it runs the event
    # loop and the actual work (startup sweep, observer) happens on a worker
    # thread. Created now, after the config form and file logging are set up.
    app = gui.applicazione()
    print(f"ℹ️ {gui.descrizione_stile()}")

    stop_event = threading.Event()
    riavvio = {'tag': None}
    esito = {'codice': 0}

    handler = CMRHandler(ocr_cfg, name_cfg, delay_riavvio, prefix)
    observer = Observer()

    def monitora():
        # ----------------------------------------------------------
        # Process existing files on startup
        # ----------------------------------------------------------
        print(f"\n🔍 Elaborazione file esistenti in: {cartella}")
        if os.path.isdir(cartella):
            for fname in os.listdir(cartella):
                if stop_event.is_set():
                    return
                if fname.startswith(prefix) and fname.endswith('.pdf'):
                    _rinomina_pdf(os.path.join(cartella, fname), ocr_cfg, name_cfg)
        else:
            print(f"⚠️ La cartella '{cartella}' non esiste. Il watcher attenderà che venga creata.")

        # ----------------------------------------------------------
        # Start watcher
        # ----------------------------------------------------------
        if stop_event.is_set():
            return
        print(f"\n👀 Monitoraggio avviato su: {cartella}")
        print("   Premi Ctrl+C per fermare.\n")
        try:
            observer.schedule(handler, cartella, recursive=False)
            observer.start()
        except FileNotFoundError:
            print(f"❌ Errore: La cartella monitorata '{cartella}' non è valida.")
            esito['codice'] = 1
            stop_event.set()
        except Exception as e:
            print(f"❌ Errore durante l'avvio del watcher: {e}")
            esito['codice'] = 1
            stop_event.set()

    aggiornatore = None
    if not _is_frozen():
        print("ℹ️ Aggiornamento automatico non attivo: esecuzione da sorgente.")
    elif not aggiornamenti_attivi:
        print("ℹ️ Aggiornamento automatico disattivato in config.ini.")
    elif not token_aggiornamenti:
        print("ℹ️ Aggiornamento automatico non attivo: manca 'token' nella sezione [Aggiornamenti] di config.ini.")
    else:
        def al_riavvio(tag):
            # Il nuovo exe è già al suo posto: si ferma tutto in modo ordinato
            # (observer.join aspetta il file in elaborazione) e lo si avvia alla fine.
            riavvio['tag'] = tag
            stop_event.set()

        aggiornatore = aggiornamento.Aggiornatore(token_aggiornamenti, sys.executable, al_riavvio)
        aggiornatore.avvia_periodico(stop_event)

    tray_icon = None
    if app is not None and _is_frozen():
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                raise RuntimeError("area di notifica assente")
            icon_image = Image.open(_get_resource_path(os.path.join('assets', 'icon.ico')))
            log_path = os.path.join(config_dir, 'cmr-renamer.log')
            tray_icon = _build_tray_icon(icon_image, ocr_cfg, name_cfg, log_path, cartella, stop_event,
                                         aggiornatore)
            tray_icon.show()
        except Exception as e:
            print(f"⚠️ Icona di system tray non disponibile: {e}")
            tray_icon = None

    lavoro = threading.Thread(target=monitora, name="monitoraggio", daemon=True)
    lavoro.start()

    if app is not None:
        # Python runs its SIGINT handler only between bytecodes, which the
        # loop's polling timer provides; the default handler would raise
        # KeyboardInterrupt inside a Qt slot instead of stopping cleanly.
        def al_ctrl_c(*_):
            print("\n🛑 Arresto...")
            stop_event.set()

        signal.signal(signal.SIGINT, al_ctrl_c)
        gui.ciclo_eventi(stop_event)
    else:
        try:
            # wait() a intervalli, non senza timeout: così Ctrl+C resta reattivo
            # anche da console, e il riavvio per aggiornamento funziona senza tray.
            while not stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            print("\n🛑 Arresto...")
    stop_event.set()

    if tray_icon is not None:
        tray_icon.hide()
    # The sweep checks stop_event between files and a pending calibration
    # returns None once the loop is over (gui.esegui_in_gui), so this ends
    # after at most the file being processed.
    lavoro.join()
    if observer.is_alive():
        observer.stop()
        observer.join()
    if riavvio['tag']:
        print(f"🔄 Riavvio sulla {riavvio['tag']}...")
        try:
            aggiornamento.riavvia(sys.executable)
        except Exception as e:
            print(f"❌ Riavvio non riuscito ({e}): avvia di nuovo CMR Renamer a mano.")
    print("👋 Monitoraggio terminato.")
    return esito['codice']