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
import configparser

import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import load_or_create_config

try:
    from tkinter import Tk, Canvas, Button, Label, Frame
    from PIL import ImageTk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


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


def _setup_file_logging(log_dir: str):
    """Redirect stdout/stderr to a log file in background mode."""
    log_path = os.path.join(log_dir, 'cmr-renamer.log')
    # Use 'a' for append mode, ensure UTF-8 encoding, replace errors
    sys.stdout = open(log_path, 'a', encoding='utf-8', errors='replace')
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


def _save_boxes_to_config(box1: tuple, box2: tuple) -> None:
    """Salva le nuove coordinate box1/box2 nel config.ini esistente."""
    config_path = os.path.join(_get_config_dir(), 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path)
    if 'OCR' not in config:
        config['OCR'] = {}
    config['OCR']['box1'] = ','.join(str(int(v)) for v in box1)
    config['OCR']['box2'] = ','.join(str(int(v)) for v in box2)
    with open(config_path, 'w') as f:
        config.write(f)


def _calibra_box(img: "Image.Image", box1: tuple, box2: tuple):
    """Mostra la pagina e permette di ridisegnare box1/box2 col mouse.

    Ritorna (nuovo_box1, nuovo_box2) se l'utente salva, altrimenti None.
    """
    if not TKINTER_AVAILABLE:
        print("⚠️ tkinter non disponibile: calibrazione box saltata.")
        return None

    MIN_DRAG = 4  # px — ignore accidental clicks/near-zero drags
    labels = {
        'box1': "box 1 (numero documento)",
        'box2': "box 2 (ragione sociale)",
    }
    colors = {'box1': 'red', 'box2': 'blue'}
    boxes = {'box1': tuple(box1), 'box2': tuple(box2)}
    state = {'active': 'box1', 'start': None, 'drag_id': None, 'result': None}
    drawn_ids: dict = {}
    select_buttons: dict = {}

    try:
        root = Tk()
        root.title("CMR Renamer - Calibrazione box OCR")

        screen_w = max(root.winfo_screenwidth() - 100, 300)
        screen_h = max(root.winfo_screenheight() - 200, 300)
        scale = min(screen_w / img.width, screen_h / img.height, 1.0)
        disp_w, disp_h = max(int(img.width * scale), 1), max(int(img.height * scale), 1)

        photo = ImageTk.PhotoImage(img.resize((disp_w, disp_h)))

        select_frame = Frame(root)
        select_frame.pack(pady=4)

        label = Label(root, text="")
        label.pack(pady=2)

        canvas = Canvas(root, width=disp_w, height=disp_h, cursor="cross")
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)

        def draw_box(name):
            x1, y1, x2, y2 = [c * scale for c in boxes[name]]
            if name in drawn_ids:
                canvas.delete(drawn_ids[name])
            drawn_ids[name] = canvas.create_rectangle(
                x1, y1, x2, y2, outline=colors[name], width=3
            )

        draw_box('box1')
        draw_box('box2')

        def set_active(name):
            state['active'] = name
            for n, btn in select_buttons.items():
                btn.config(relief=("sunken" if n == name else "raised"))
            label.config(text=f"Box attivo: {labels[name]}. Trascina col mouse per ridisegnarlo.")

        for name in ('box1', 'box2'):
            select_buttons[name] = Button(
                select_frame, text=labels[name], command=lambda n=name: set_active(n)
            )
            select_buttons[name].pack(side="left", padx=5)

        set_active('box1')

        def on_press(event):
            state['start'] = (event.x, event.y)

        def on_drag(event):
            if state['start'] is None:
                return
            if state['drag_id'] is not None:
                canvas.delete(state['drag_id'])
            x0, y0 = state['start']
            state['drag_id'] = canvas.create_rectangle(
                x0, y0, event.x, event.y, outline=colors[state['active']], width=2, dash=(4, 2)
            )

        def on_release(event):
            if state['drag_id'] is not None:
                canvas.delete(state['drag_id'])
                state['drag_id'] = None
            if state['start'] is None:
                return
            x0, y0 = state['start']
            state['start'] = None
            x1, y1 = event.x, event.y

            if abs(x1 - x0) < MIN_DRAG or abs(y1 - y0) < MIN_DRAG:
                return  # too small to be an intentional box — ignore

            x0 = min(max(x0, 0), disp_w)
            x1 = min(max(x1, 0), disp_w)
            y0 = min(max(y0, 0), disp_h)
            y1 = min(max(y1, 0), disp_h)
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))

            name = state['active']
            boxes[name] = (int(x0 / scale), int(y0 / scale), int(x1 / scale), int(y1 / scale))
            draw_box(name)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        btn_frame = Frame(root)
        btn_frame.pack(pady=8)

        def on_save():
            state['result'] = (boxes['box1'], boxes['box2'])
            root.destroy()

        def on_cancel():
            state['result'] = None
            root.destroy()

        Button(btn_frame, text="Salva", command=on_save).pack(side="left", padx=5)
        Button(btn_frame, text="Annulla", command=on_cancel).pack(side="left", padx=5)

        root.mainloop()
        return state['result']
    except Exception as e:
        print(f"⚠️ Errore durante la calibrazione dei box: {e}")
        return None


def _rinomina_pdf(pdf_path: str, ocr_cfg: dict, name_cfg: dict) -> None:
    """Esegue OCR e rinomina il PDF con il testo estratto."""
    try:
        immagini = convert_from_path(pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1)
        img = immagini[0]

        if ocr_cfg['show_rects']:
            nuovi_box = _calibra_box(img, ocr_cfg['box1'], ocr_cfg['box2'])
            if nuovi_box:
                ocr_cfg['box1'], ocr_cfg['box2'] = nuovi_box
                _save_boxes_to_config(ocr_cfg['box1'], ocr_cfg['box2'])
                print(f"✅ Nuove coordinate salvate → box1={ocr_cfg['box1']} box2={ocr_cfg['box2']}")

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

        print(f"⏳ Attendo: {os.path.basename(path)}")
        if not _file_pronto(path):
            print(f"⚠️ Timeout attesa: {os.path.basename(path)}")
        print(f"✔ Elaboro: {path}")
        _rinomina_pdf(path, self.ocr_cfg, self.name_cfg)


# ──────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────

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
    elif _is_frozen() and config_exists:
        # Frozen + config exists → background mode (no console)
        _setup_file_logging(config_dir)
        cfg = load_or_create_config(config_path=config_path)
    else:
        # Normal Python execution → use console as-is
        cfg = load_or_create_config(config_path=config_path)

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
            if fname.startswith(prefix) and fname.endswith('.pdf'):
                _rinomina_pdf(os.path.join(cartella, fname), ocr_cfg, name_cfg)
    else:
        print(f"⚠️ La cartella '{cartella}' non esiste. Il watcher attenderà che venga creata.")

    # ----------------------------------------------------------
    # Start watcher
    # ----------------------------------------------------------
    print(f"\n👀 Monitoraggio avviato su: {cartella}")
    print("   Premi Ctrl+C per fermare.\n")

    handler = CMRHandler(ocr_cfg, name_cfg, delay_riavvio, prefix)
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