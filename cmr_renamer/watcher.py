"""File system watcher for CMR Renamer.

Watches a directory for new PDF files and triggers OCR-based renaming.
When frozen (PyInstaller --windowed) the executable has no console by default;
if config.ini is missing a console is allocated temporarily for setup,
then freed. In background mode, output is logged to a file.
"""

import os
import re
import sys
import time
import ctypes  # For Windows console manipulation

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageDraw
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import load_or_create_config, CONFIG_FILE


# ──────────────────────────────────────────────────────────────
# Console management (Windows only)
# ──────────────────────────────────────────────────────────────

def _alloc_console():
    """Allocate a Windows console for interactive setup."""
    kernel32 = ctypes.windll.kernel32
    if kernel32.AllocConsole():
        # Redirect stdout, stderr, and stdin to the new console
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')
        # Set console title (optional)
        ctypes.windll.kernel32.SetConsoleTitleW("CMR Renamer Setup")


def _free_console():
    """Free the allocated Windows console."""
    import ctypes
    ctypes.windll.kernel32.FreeConsole()


def _setup_file_logging(log_dir: str):
    """Redirect stdout/stderr to a log file in background mode."""
    log_path = os.path.join(log_dir, 'cmr-renamer.log')
    # Use 'a' for append mode, ensure UTF-8 encoding
    sys.stdout = open(log_path, 'a', encoding='utf-8')
    sys.stderr = sys.stdout  # Redirect stderr to the same log file


# ──────────────────────────────────────────────────────────────
# OCR & rename helpers
# ──────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────
# File system watcher
# ──────────────────────────────────────────────────────────────

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


def run() -> int:
    """Main entry point: load config, process existing files, start watcher.

    Returns 0 on success.
    """
    config_dir = _get_config_dir()
    config_path = os.path.join(config_dir, 'config.ini')
    config_exists = os.path.exists(config_path)

    # ── Background vs interactive mode ─────────────────────
    if _is_frozen() and not config_exists:
        # Frozen + no config → allocate console for setup
        _alloc_console()
        print("\n=== CMR Renamer - Configurazione Iniziale ===\n")
        try:
            cfg = load_or_create_config()
        finally:
            _free_console() # Ensure console is freed even if setup fails
    elif _is_frozen() and config_exists:
        # Frozen + config exists → background mode (no console)
        _setup_file_logging(config_dir)
        cfg = load_or_create_config()
    else:
        # Normal Python execution → use console as-is
        cfg = load_or_create_config()

    # ── Parse config ───────────────────────────────────────
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

    # ----------------------------------------------------------
    # Process existing files on startup
    # ----------------------------------------------------------
    print(f"\n🔍 Elaborazione file esistenti in: {cartella}")
    if os.path.isdir(cartella):
        for fname in os.listdir(cartella):
            if fname.startswith('DOC') and fname.endswith('.pdf'):
                _rinomina_pdf(os.path.join(cartella, fname), ocr_cfg, name_cfg)
    else:
        print(f"⚠️ La cartella '{cartella}' non esiste. Il watcher attenderà che venga creata.")

    # ----------------------------------------------------------
    # Start watcher
    # ----------------------------------------------------------
    print(f"\n👀 Monitoraggio avviato su: {cartella}")
    print("   Premi Ctrl+C per fermare.\n")

    handler = CMRHandler(ocr_cfg, name_cfg, delay_riavvio)
    observer = Observer()
    try:
        observer.schedule(handler, cartella, recursive=False)
        observer.start()
    except FileNotFoundError:
        print(f"❌ Errore: La cartella monitorata '{cartella}' non è valida.")
        return 1 # Indicate error
    except Exception as e:
        print(f"❌ Errore durante l'avvio del watcher: {e}")
        return 1 # Indicate error

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arresto...")
        observer.stop()

    observer.join()
    print("👋 Monitoraggio terminato.")
    return 0