"""File system watcher for CMR Renamer.

Watches a directory for new PDF files and triggers OCR-based renaming.
"""

import os
import re
import time

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageDraw
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import load_or_create_config


def _pulisci_nome(testo: str, max_len: int, rimuovi_zeri: bool) -> str:
    """Pulisce il testo OCR per usarlo come nome file."""
    clean = re.sub(r'[^\w\s.-]', '', testo).replace('\n', ' ').strip()
    if len(clean) > max_len:
        clean = clean[:max_len]
    if rimuovi_zeri:
        clean = re.sub(r'^0+', '', clean)
    return clean


def _file_pronto(path: str, timeout: int = 5) -> bool:
    """Aspetta che il file sia finito di scrivere dalla fotocopiatrice."""
    try:
        for _ in range(timeout):
            size1 = os.path.getsize(path)
            time.sleep(1)
            size2 = os.path.getsize(path)
            if size1 == size2:
                return True
        return False
    except OSError:
        return False


def _rinomina_pdf(pdf_path: str, ocr_cfg: dict, name_cfg: dict) -> None:
    """Esegue OCR e rinomina il PDF con il testo estratto."""
    try:
        immagini = convert_from_path(pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1)
        img = immagini[0]

        if ocr_cfg['show_rects']:
            draw = ImageDraw.Draw(img)
            draw.rectangle(ocr_cfg['box1'], outline="red", width=3)
            draw.rectangle(ocr_cfg['box2'], outline="red", width=3)
            img.show()

        testo1 = pytesseract.image_to_string(
            img.crop(ocr_cfg['box1']), lang=ocr_cfg['lang']
        )
        testo2 = pytesseract.image_to_string(
            img.crop(ocr_cfg['box2']), lang=ocr_cfg['lang']
        )

        base = f"{_pulisci_nome(testo1, name_cfg['max_length'], name_cfg['remove_leading_zeros'])} {_pulisci_nome(testo2, name_cfg['max_length'], name_cfg['remove_leading_zeros'])}".strip()
        if not base:
            base = "documento_senza_nome"

        dest_dir = os.path.dirname(pdf_path)
        nuovo_path = os.path.join(dest_dir, f"{base}.pdf")

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

    except Exception as e:
        print(f"❌ Errore per '{os.path.basename(pdf_path)}': {e}")


class CMRHandler(FileSystemEventHandler):
    """Event handler che processa i PDF appena creati/spostati/modificati."""

    def __init__(self, ocr_cfg: dict, name_cfg: dict, delay: int):
        super().__init__()
        self.processati: dict[str, float] = {}
        self.ocr_cfg = ocr_cfg
        self.name_cfg = name_cfg
        self.delay = delay

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
        if not os.path.basename(path).startswith('DOC'):
            return

        now = time.time()
        if path in self.processati and now - self.processati[path] < self.delay:
            return
        self.processati[path] = now

        print(f"⏳ Attendo: {os.path.basename(path)}")
        if not _file_pronto(path):
            print(f"⚠️ Timeout attesa: {os.path.basename(path)}")
        print(f"✔ Elaboro: {path}")
        _rinomina_pdf(path, self.ocr_cfg, self.name_cfg)


def run() -> None:
    """Punto d'ingresso principale: carica config, processa i PDF esistenti, avvia il watcher."""
    cfg = load_or_create_config()

    cartella = cfg['Watcher']['folder']
    delay_riavvio = int(cfg['Watcher']['delay_riavvio'])

    ocr_cfg = {
        'box1': tuple(map(int, cfg['OCR']['box1'].split(','))),
        'box2': tuple(map(int, cfg['OCR']['box2'].split(','))),
        'show_rects': cfg['OCR'].getboolean('show_rects'),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
    }

    name_cfg = {
        'max_length': int(cfg['Filename']['max_length']),
        'remove_leading_zeros': cfg['Filename'].getboolean('remove_leading_zeros'),
    }

    # Elabora PDF già presenti all'avvio
    print(f"\n🔍 Elaborazione file esistenti in: {cartella}")
    if os.path.isdir(cartella):
        for fname in os.listdir(cartella):
            if fname.startswith('DOC') and fname.endswith('.pdf'):
                _rinomina_pdf(os.path.join(cartella, fname), ocr_cfg, name_cfg)
    else:
        print(f"⚠️ La cartella '{cartella}' non esiste. Il watcher attenderà che venga creata.")

    # Avvia il watcher
    print(f"\n👀 Monitoraggio avviato su: {cartella}")
    print("   Premi Ctrl+C per fermare.\n")

    handler = CMRHandler(ocr_cfg, name_cfg, delay_riavvio)
    observer = Observer()
    observer.schedule(handler, cartella, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arresto...")
        observer.stop()

    observer.join()
    print("👋 Monitoraggio terminato.")