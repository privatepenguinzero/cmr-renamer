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
import configparser

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageOps
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import load_or_create_config

try:
    from tkinter import Tk, Canvas, Button, Label, Frame, Scrollbar, Listbox
    from PIL import ImageTk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False


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

def _pulisci_nome(testo: str, max_len: int, rimuovi_zeri: bool) -> str:
    """Pulisce il testo OCR per usarlo come nome file."""
    clean = re.sub(r'[^\w\s.-]', '', testo).replace('\n', ' ').strip()
    if len(clean) > max_len:
        clean = clean[:max_len]
    if rimuovi_zeri:
        clean = re.sub(r'^0+', '', clean)
    return clean


def _render_pdf_page(path: str, dpi: int) -> "Image.Image":
    """Renderizza la pagina 1 di un PDF come immagine PIL."""
    immagini = convert_from_path(
        path, dpi=dpi, first_page=1, last_page=1,
        poppler_path=_get_poppler_path(),
    )
    return immagini[0]


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Migliora un crop prima dell'OCR: scala di grigi, contrasto, binarizzazione.

    Soglia fissa (128), non derivata per immagine — punto di partenza pensato
    per tuning manuale (vedi verifica del piano), non un default definitivo.
    """
    gray = img.convert('L')
    contrasted = ImageOps.autocontrast(gray)
    return contrasted.point(lambda p: 255 if p > 128 else 0)


_ANCHOR_DARK_THRESHOLD = 128  # stessa soglia di _preprocess_for_ocr
_ANCHOR_MIN_DARK_FRACTION = 0.03  # frazione minima di pixel scuri per considerare una riga/colonna "contenuto"
_ANCHOR_MIN_RUN = 4  # posizioni consecutive richieste, per ignorare rumore isolato (graffette, polvere)


def _content_profile(binaria: "Image.Image", axis_size: int, vertical: bool) -> list:
    """Frazione di pixel scuri per riga (vertical=True) o per colonna, via resize con filtro BOX."""
    if vertical:
        small = binaria.resize((1, axis_size), Image.BOX)
    else:
        small = binaria.resize((axis_size, 1), Image.BOX)
    return [(255 - p) / 255 for p in small.getdata()]


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


MIN_BOXES = 2
MAX_BOXES = 5

# Guards the "open calibrator + save config" critical section so the tray's
# recalibrate action and the per-file auto-calibration (on the watchdog
# observer thread) can never both hold an open Tk mainloop or write
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
    for i in range(len(boxes) + 1, MAX_BOXES + 1):
        config['OCR'].pop(f'box{i}', None)
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


def _calibra_box(pdf_paths: list, initial_path: str, boxes: list, dpi: int):
    """Mostra la pagina 1 di un PDF a scelta tra `pdf_paths` e permette di ridisegnare 2-5 box col mouse.

    `pdf_paths` è l'elenco dei PDF della cartella monitorata, selezionabili da una lista laterale
    per confrontare visivamente se i box calibrati si applicano bene a più documenti; il cambio file
    ridisegna solo l'immagine di sfondo, i box restano nelle stesse coordinate. `initial_path` è il
    file mostrato all'apertura (preselezionato in lista). `boxes` è una lista di partenza di 2-5
    tuple (x1,y1,x2,y2). Se l'utente salva, ritorna {'boxes': [...], 'anchor': (x,y) | None} —
    l'ancora è rilevata sull'immagine visualizzata al momento del salvataggio (non necessariamente
    quella di `initial_path`, se nel frattempo si è passati a un altro file dalla lista). Ritorna
    None se l'utente annulla.
    """
    if not TKINTER_AVAILABLE:
        print("⚠️ tkinter non disponibile: calibrazione box saltata.")
        return None

    MIN_DRAG = 4  # px — ignore accidental clicks/near-zero drags
    MAX_ZOOM = 6.0  # relative to the initial fit-to-screen view
    MAX_DIM = 8000  # px safety cap on the rendered (zoomed) image size

    image_cache: dict = {}

    def get_image(path: str) -> "Image.Image":
        if path not in image_cache:
            image_cache[path] = _render_pdf_page(path, dpi)
        return image_cache[path]

    state = {
        'boxes': list(boxes),
        'active': 0, 'start': None, 'drag_id': None, 'result': None,
        'zoom': 1.0, 'photo': None, 'img': None, 'current_path': initial_path,
    }
    drawn_ids: dict = {}
    select_buttons: dict = {}

    try:
        state['img'] = get_image(initial_path)
        root = Tk()
        root.title("CMR Renamer - Calibrazione box OCR")

        screen_w = max(root.winfo_screenwidth() - 150, 300)
        screen_h = max(root.winfo_screenheight() - 260, 300)
        base_scale = min(screen_w / state['img'].width, screen_h / state['img'].height, 1.0)
        viewport_w = max(int(state['img'].width * base_scale), 1)
        viewport_h = max(int(state['img'].height * base_scale), 1)
        max_zoom = min(
            MAX_ZOOM,
            MAX_DIM / (state['img'].width * base_scale),
            MAX_DIM / (state['img'].height * base_scale),
        )

        top_frame = Frame(root)
        top_frame.pack(pady=4)

        select_frame = Frame(top_frame)
        select_frame.pack(side="left")

        count_frame = Frame(top_frame)
        count_frame.pack(side="left", padx=20)

        zoom_frame = Frame(top_frame)
        zoom_frame.pack(side="left", padx=20)

        label = Label(root, text="")
        label.pack(pady=2)

        body_frame = Frame(root)
        body_frame.pack()

        sidebar_frame = Frame(body_frame)
        sidebar_frame.pack(side="left", fill="y", padx=(4, 4))

        Label(sidebar_frame, text="File nella cartella:").pack(anchor="w")
        file_listbox = Listbox(sidebar_frame, width=32, height=28, exportselection=False)
        file_scrollbar = Scrollbar(sidebar_frame, orient="vertical", command=file_listbox.yview)
        file_listbox.configure(yscrollcommand=file_scrollbar.set)
        file_listbox.pack(side="left", fill="y")
        file_scrollbar.pack(side="left", fill="y")

        for path in pdf_paths:
            file_listbox.insert("end", os.path.basename(path))
        if initial_path in pdf_paths:
            initial_index = pdf_paths.index(initial_path)
            file_listbox.selection_set(initial_index)
            file_listbox.see(initial_index)

        canvas_frame = Frame(body_frame)
        canvas_frame.pack(side="left")

        canvas = Canvas(canvas_frame, width=viewport_w, height=viewport_h, cursor="cross", bg="#333333")
        vbar = Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        hbar = Scrollbar(root, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        canvas.grid(row=0, column=0)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.pack(fill="x")

        image_item = canvas.create_image(0, 0, anchor="nw")

        def total_scale():
            return base_scale * state['zoom']

        def draw_box(index):
            scale = total_scale()
            x1, y1, x2, y2 = [c * scale for c in state['boxes'][index]]
            if index in drawn_ids:
                canvas.delete(drawn_ids[index])
            drawn_ids[index] = canvas.create_rectangle(
                x1, y1, x2, y2, outline=BOX_COLORS[index], width=3
            )

        def draw_all_boxes():
            for old_id in drawn_ids.values():
                canvas.delete(old_id)
            drawn_ids.clear()
            for i in range(len(state['boxes'])):
                draw_box(i)

        def render():
            scale = total_scale()
            disp_w = max(int(state['img'].width * scale), 1)
            disp_h = max(int(state['img'].height * scale), 1)
            state['photo'] = ImageTk.PhotoImage(state['img'].resize((disp_w, disp_h)))
            canvas.itemconfig(image_item, image=state['photo'])
            canvas.configure(scrollregion=(0, 0, disp_w, disp_h))
            draw_all_boxes()
            zoom_label.config(text=f"{int(state['zoom'] * 100)}%")

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

        def zoom_reset():
            set_zoom(1.0)

        def on_mousewheel(event):
            # Windows/Mac deliver <MouseWheel> with event.delta; Linux uses
            # separate Button-4/Button-5 events instead (handled below).
            if event.delta > 0:
                zoom_in()
            else:
                zoom_out()

        canvas.bind("<MouseWheel>", on_mousewheel)
        canvas.bind("<Button-4>", lambda e: zoom_in())
        canvas.bind("<Button-5>", lambda e: zoom_out())

        Button(zoom_frame, text="−", command=zoom_out, width=3).pack(side="left")
        zoom_label = Label(zoom_frame, text="100%", width=6)
        zoom_label.pack(side="left", padx=4)
        Button(zoom_frame, text="+", command=zoom_in, width=3).pack(side="left")
        Button(zoom_frame, text="Reset zoom", command=zoom_reset).pack(side="left", padx=8)

        def set_active(index):
            state['active'] = index
            for i, btn in select_buttons.items():
                btn.config(relief=("sunken" if i == index else "raised"))
            label.config(text=f"Box attivo: {_box_label(index)}. Trascina col mouse per ridisegnarlo (rotellina per zoomare).")

        def update_count_buttons():
            add_btn.config(state=("disabled" if len(state['boxes']) >= MAX_BOXES else "normal"))
            remove_btn.config(state=("disabled" if len(state['boxes']) <= MIN_BOXES else "normal"))

        def rebuild_select_buttons():
            for btn in select_buttons.values():
                btn.destroy()
            select_buttons.clear()
            for i in range(len(state['boxes'])):
                btn = Button(
                    select_frame, text=_box_label(i), command=lambda n=i: set_active(n),
                    bg=BOX_COLORS[i], fg="white", activebackground=BOX_COLORS[i], activeforeground="white",
                )
                btn.pack(side="left", padx=5)
                select_buttons[i] = btn
            update_count_buttons()

        def add_box():
            if len(state['boxes']) >= MAX_BOXES:
                return
            state['boxes'].append(_default_box(len(state['boxes'])))
            rebuild_select_buttons()
            set_active(len(state['boxes']) - 1)
            render()

        def remove_box():
            if len(state['boxes']) <= MIN_BOXES:
                return
            removed = state['active']
            del state['boxes'][removed]
            rebuild_select_buttons()
            set_active(max(removed - 1, 0))
            render()

        add_btn = Button(count_frame, text="+ Box", command=add_box)
        add_btn.pack(side="left", padx=2)
        remove_btn = Button(count_frame, text="− Box", command=remove_box)
        remove_btn.pack(side="left", padx=2)

        def on_file_select(event=None):
            selection = file_listbox.curselection()
            if not selection:
                return
            path = pdf_paths[selection[0]]
            if path == state['current_path']:
                return
            try:
                new_img = get_image(path)
            except Exception as e:
                print(f"⚠️ Impossibile aprire '{os.path.basename(path)}': {e}")
                file_listbox.selection_clear(0, "end")
                file_listbox.selection_set(pdf_paths.index(state['current_path']))
                return
            state['img'] = new_img
            state['current_path'] = path
            render()

        file_listbox.bind("<<ListboxSelect>>", on_file_select)

        rebuild_select_buttons()
        set_active(0)
        render()

        def on_press(event):
            state['start'] = (canvas.canvasx(event.x), canvas.canvasy(event.y))

        def on_drag(event):
            if state['start'] is None:
                return
            if state['drag_id'] is not None:
                canvas.delete(state['drag_id'])
            x0, y0 = state['start']
            cx, cy = canvas.canvasx(event.x), canvas.canvasy(event.y)
            state['drag_id'] = canvas.create_rectangle(
                x0, y0, cx, cy, outline=BOX_COLORS[state['active']], width=2, dash=(4, 2)
            )

        def on_release(event):
            if state['drag_id'] is not None:
                canvas.delete(state['drag_id'])
                state['drag_id'] = None
            if state['start'] is None:
                return
            x0, y0 = state['start']
            state['start'] = None
            x1, y1 = canvas.canvasx(event.x), canvas.canvasy(event.y)

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

            index = state['active']
            state['boxes'][index] = (int(x0 / scale), int(y0 / scale), int(x1 / scale), int(y1 / scale))
            draw_box(index)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        btn_frame = Frame(root)
        btn_frame.pack(pady=8)

        def on_save():
            state['result'] = {'boxes': list(state['boxes']), 'anchor': _detect_content_anchor(state['img'])}
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


def _build_tray_icon(icon_image: "Image.Image", ocr_cfg: dict, log_path: str,
                      cartella: str, stop_event: "threading.Event") -> "pystray.Icon":
    """Crea l'icona di system tray con il menu di controllo del background mode."""

    def _open_log(icon, item):
        try:
            os.startfile(os.path.normpath(log_path))
        except Exception as e:
            print(f"⚠️ Impossibile aprire il log: {e}")

    def _open_folder(icon, item):
        try:
            os.startfile(os.path.normpath(cartella))
        except Exception as e:
            print(f"⚠️ Impossibile aprire la cartella: {e}")

    def _recalibra(icon, item):
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
            risultato = _calibra_box(pdf_paths, pdf_paths[0], boxes_seed, ocr_cfg['dpi'])
            if risultato:
                ocr_cfg['boxes'] = risultato['boxes']
                ocr_cfg['anchor'] = risultato['anchor']
                _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
        except Exception as e:
            print(f"⚠️ Errore durante la ricalibrazione: {e}")
        finally:
            _calibration_lock.release()

    def _exit(icon, item):
        icon.stop()
        stop_event.set()

    menu = pystray.Menu(
        pystray.MenuItem("Apri log", _open_log),
        pystray.MenuItem("Apri cartella monitorata", _open_folder),
        pystray.MenuItem("Ricalibra box", _recalibra),
        pystray.MenuItem("Esci", _exit),
    )
    return pystray.Icon("cmr-renamer", icon_image, "CMR Renamer", menu)


MAX_ANCHOR_SHIFT_MM = 15  # oltre questa soglia lo spostamento rilevato è considerato inaffidabile


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

    dx = current[0] - reference[0]
    dy = current[1] - reference[1]
    max_shift_px = ocr_cfg['dpi'] * MAX_ANCHOR_SHIFT_MM / 25.4
    if abs(dx) > max_shift_px or abs(dy) > max_shift_px:
        print(f"⚠️ Spostamento rilevato ({dx}, {dy}px) oltre la soglia plausibile: uso i box calibrati senza correzione deriva.")
        return boxes

    return [(x1 + dx, y1 + dy, x2 + dx, y2 + dy) for (x1, y1, x2, y2) in boxes]


def _rinomina_pdf(pdf_path: str, ocr_cfg: dict, name_cfg: dict) -> None:
    """Esegue OCR e rinomina il PDF con il testo estratto."""
    try:
        img = _render_pdf_page(pdf_path, ocr_cfg['dpi'])

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
                    risultato = _calibra_box(pdf_paths, pdf_path, boxes_seed, ocr_cfg['dpi'])
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
        for box in _resolve_crop_boxes(img, ocr_cfg):
            crop = _preprocess_for_ocr(img.crop(box))
            testo = pytesseract.image_to_string(crop, lang=ocr_cfg['lang'])
            pulito = _pulisci_nome(testo, name_cfg['max_length'], name_cfg['remove_leading_zeros'])
            if pulito:
                parti.append(pulito)

        base = " ".join(parti).strip()
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
    ocr_cfg = {
        'boxes': _load_boxes_from_config(cfg['OCR']),
        'anchor': _load_anchor_from_config(cfg['OCR']),
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
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

    tray_icon = None
    stop_event = threading.Event()
    if _is_frozen() and PYSTRAY_AVAILABLE:
        try:
            icon_image = Image.open(_get_resource_path(os.path.join('assets', 'icon.ico')))
            log_path = os.path.join(config_dir, 'cmr-renamer.log')
            tray_icon = _build_tray_icon(icon_image, ocr_cfg, log_path, cartella, stop_event)
            threading.Thread(target=tray_icon.run, daemon=True).start()
        except Exception as e:
            print(f"⚠️ Icona di system tray non disponibile: {e}")
            tray_icon = None

    try:
        if tray_icon is not None:
            stop_event.wait()
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arresto...")

    observer.stop()
    observer.join()
    print("👋 Monitoraggio terminato.")
    return 0