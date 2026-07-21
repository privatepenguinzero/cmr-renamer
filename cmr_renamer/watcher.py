"""File system watcher for CMR Renamer.

Watches a directory for new PDF files and triggers renaming based on OCR content.
"""

import os
import time
from typing import Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageDraw

from .config import config
from .ocr import OCR_BOX1, OCR_BOX2, OCR_SHOW_RECTS, OCR_LANG, OCR_DPI


def _pulisci_nome(testo: str, max_length: int, remove_leading_zeros: bool) -> str:
    """Clean extracted text for use in a filename."""
    import re
    clean = re.sub(r'[^\w\s.-]', '', testo).replace('\n', ' ').strip()
    if len(clean) > max_length:
        clean = clean[:max_length]
    if remove_leading_zeros:
        clean = re.sub(r'^0+', '', clean)
    return clean


def _wait_for_file_ready(path: str, timeout: int = 5) -> bool:
    """Wait for a file to finish being written."""
    try:
        for _ in range(timeout):
            size1 = os.path.getsize(path)
            time.sleep(1)
            size2 = os.path.getsize(path)
            if size1 == size2:
                return True
        return False
    except:
        return False


def _rename_pdf_file(pdf_path: str, max_length: int, remove_leading_zeros: bool) -> None:
    """Rename a single PDF file based on OCR content."""
    try:
        # Convert first page of PDF to image
        immagini = convert_from_path(pdf_path, dpi=OCR_DPI, first_page=1, last_page=1)
        img = immagini[0]

        # Optionally draw debug rectangles
        if OCR_SHOW_RECTS:
            draw = ImageDraw.Draw(img)
            draw.rectangle(OCR_BOX1, outline="red", width=3)
            draw.rectangle(OCR_BOX2, outline="red", width=3)
            img.show()

        # Extract text from the two regions
        ritaglio1 = img.crop(OCR_BOX1)
        ritaglio2 = img.crop(OCR_BOX2)

        testo1 = pytesseract.image_to_string(ritaglio1, lang=OCR_LANG)
        testo2 = pytesseract.image_to_string(ritaglio2, lang=OCR_LANG)

        # Clean the extracted text
        nome1 = _pulisci_nome(testo1, max_length, remove_leading_zeros)
        nome2 = _pulisci_nome(testo2, max_length, remove_leading_zeros)

        # Build the new filename
        nuovo_nome_base = f"{nome1} {nome2}".strip()
        if not nuovo_nome_base:
            nuovo_nome_base = "documento_senza_nome"

        nuovo_nome_file = nuovo_nome_base + ".pdf"
        cartella_doc = os.path.dirname(pdf_path)
        nuovo_path = os.path.join(cartella_doc, nuovo_nome_file)

        # Handle filename collisions
        if not os.path.exists(nuovo_path):
            os.rename(pdf_path, nuovo_path)
            print(f"✅ Rinominato '{os.path.basename(pdf_path)}' → '{nuovo_nome_file}'")
        else:
            counter = 1
            base_name_without_ext = nuovo_nome_base
            while os.path.exists(nuovo_path):
                nuovo_nome_file = f"{base_name_without_ext} ({counter}).pdf"
                nuovo_path = os.path.join(cartella_doc, nuovo_nome_file)
                counter += 1
            os.rename(pdf_path, nuovo_path)
            print(f"✅ Rinominato '{os.path.basename(pdf_path)}' → '{nuovo_nome_file}' (conflitto risolto)")

    except Exception as e:
        print(f"❌ Errore OCR per '{os.path.basename(pdf_path)}': {e}")


class CMRHandler(FileSystemEventHandler):
    """File system event handler for CMR renamer."""

    def __init__(self, max_length: int, remove_leading_zeros: bool, delay_riavvio: int):
        super().__init__()
        self.processati = {}  # path -> timestamp
        self.max_length = max_length
        self.remove_leading_zeros = remove_leading_zeros
        self.delay_riavvio = delay_riavvio

    def on_created(self, event):
        if not event.is_directory:
            self._process_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._process_file(event.dest_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._process_file(event.src_path)

    def _process_file(self, path: str) -> None:
        if not path:
            return
        if not path.endswith(".pdf"):
            return
        if not os.path.basename(path).startswith("DOC"):
            return

        now = time.time()

        # Debounce: avoid processing the same file too frequently
        if path in self.processati:
            if now - self.processati[path] < self.delay_riavvio:
                return

        self.processati[path] = now

        # Wait for file to be completely written
        print(f"⏳ Attendo che il file sia pronto: {os.path.basename(path)}")
        if not _wait_for_file_ready(path):
            print(f"⚠️ Timeout nell'attesa del file: {os.path.basename(path)}")
            # Continue anyway - the file might still be usable

        print(f"✔ File pronto: {path}")
        _rename_pdf_file(path, self.max_length, self.remove_leading_zeros)


def watch_directory(cartella: str, max_length: int, remove_leading_zeros: bool, delay_riavvio: int) -> None:
    """Start watching a directory for new PDF files."""
    print(f"👀 Avvio monitoraggio della cartella: {cartella}")
    print(f"   Lunghezza massimo nome: {max_length}")
    print(f"   Rimuovi zeri iniziali: {remove_leading_zeros}")
    print(f"   Delay riavvio: {delay_riavvio} secondi")
    print("   Premi Ctrl+C per fermare.\n")

    # Process existing files that match the pattern
    print("🔍 Elaborazione file esistenti...")
    for filename in os.listdir(cartella):
        if filename.startswith("DOC") and filename.endswith(".pdf"):
            fullpath = os.path.join(cartella, filename)
            print(f"   Trovato: {filename}")
            _rename_pdf_file(fullpath, max_length, remove_leading_zeros)
    print("✅ Elaborazione file esistenti completata.\n")

    # Set up the observer
    event_handler = CMRHandler(max_length, remove_leading_zeros, delay_riavvio)
    observer = Observer()
    observer.schedule(event_handler, cartella, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arresto del monitoraggio...")
        observer.stop()

    observer.join()
    print("👋 Monitoraggio terminato.")


def run() -> None:
    """Main entry point for the CMR Renamer application."""
    # Load configuration
    cfg = config.load_or_create_config()

    # Extract configuration values
    cartella = config.Watcher['folder']
    delay_riavvio = int(config.Watcher['delay_riavvio'])
    max_length = int(config.FILENAME['max_length'])
    remove_leading_zeros = config.FILENAME.getboolean('remove_leading_zeros')

    # Update OCR configuration from config file
    global OCR_BOX1, OCR_BOX2, OCR_SHOW_RECTS, OCR_LANG, OCR_DPI
    OCR_BOX1 = tuple(map(int, config.OCR['box1'].split(',')))
    OCR_BOX2 = tuple(map(int, config.OCR['box2'].split(',')))
    OCR_SHOW_RECTS = config.OCR.getboolean('show_rects')
    OCR_LANG = config.OCR['lang']
    OCR_DPI = int(config.OCR['dpi'])

    # Start watching the directory
    watch_directory(cartella, max_length, remove_leading_zeros, delay_riavvio)