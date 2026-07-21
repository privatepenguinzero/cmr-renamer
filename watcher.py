import sys
import os
import time
import re
import configparser
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pdf2image import convert_from_path
from PIL import Image, ImageDraw
import pytesseract

# ──────────────────────────────────────────────────────────────────────
# Determine base directory for config.ini
# ──────────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as a PyInstaller executable — config lives next to the .exe
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # Running as a normal Python script — config lives next to watcher.py
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

# Try to import tkinter for folder dialog, fallback to console if not available
try:
    from tkinter import Tk
    from tkinter.filedialog import askdirectory
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


def prompt_console(prompt_text, default=None):
    """Prompt user for input in console with optional default"""
    if default is not None:
        user_input = input(f"{prompt_text} [{default}]: ").strip()
        if user_input == '':
            return default
        return user_input
    else:
        return input(prompt_text + ": ").strip()


def prompt_for_folder():
    """Prompt user to select a folder using GUI if available, else console"""
    if TKINTER_AVAILABLE:
        try:
            root = Tk()
            root.withdraw()
            folder = askdirectory(title="Seleziona la cartella da monitorare")
            root.destroy()
            if folder:
                return folder
            print("Nessuna cartella selezionata. Inserisci il percorso manualmente.")
        except Exception as e:
            print(f"Errore nell'apertura della finestra di dialogo: {e}. Passando all'input console.")
    return prompt_console("Inserisci il percorso completo della cartella da monitorare")


def load_or_create_config():
    """Load configuration from config.ini — create it with guided prompts if missing."""
    config = configparser.ConfigParser()

    if not os.path.exists(CONFIG_FILE):
        print("\n=== FILE DI CONFIGURAZIONE NON TROVATO ===")
        print(f"Percorso atteso: {CONFIG_FILE}")
        print("Creazione guidata — inserisci i valori richiesti oppure premi Invio per usare il default.\n")

        config['Watcher'] = {}
        config['Watcher']['folder'] = prompt_for_folder()
        config['Watcher']['delay_riavvio'] = prompt_console(
            "Delay tra rilevamenti ripetuti (secondi)", "3"
        )

        config['OCR'] = {}
        config['OCR']['box1'] = prompt_console(
            "Coordinate box1 — numero documento (x1,y1,x2,y2)", "595,1615,760,1750"
        )
        config['OCR']['box2'] = prompt_console(
            "Coordinate box2 — ragione sociale (x1,y1,x2,y2)", "230,720,1085,785"
        )
        show_rects_default = prompt_console(
            "Mostrare rettangoli di debug sulle immagini? (True/False)", "False"
        ).lower()
        config['OCR']['show_rects'] = str(show_rects_default in ['true', '1', 'yes', 'y'])
        config['OCR']['lang'] = prompt_console(
            "Lingua OCR (es: eng, ita, fra)", "eng"
        )
        config['OCR']['dpi'] = prompt_console(
            "DPI per conversione PDF", "300"
        )

        config['Filename'] = {}
        config['Filename']['max_length'] = prompt_console(
            "Lunghezza massima per ogni parte del nome file", "60"
        )
        remove_zeros_default = prompt_console(
            "Rimuovere zeri iniziali dai numeri? (True/False)", "True"
        ).lower()
        config['Filename']['remove_leading_zeros'] = str(remove_zeros_default in ['true', '1', 'yes', 'y'])

        with open(CONFIG_FILE, 'w') as f:
            config.write(f)
        print(f"\n✅ File di configurazione creato: {CONFIG_FILE}\n")
    else:
        config.read(CONFIG_FILE)
        print(f"📂 File di configurazione caricato: {CONFIG_FILE}")

    return config


# ──────────────────────────────────────────────────────────────────────
# Load / create configuration
# ──────────────────────────────────────────────────────────────────────
config = load_or_create_config()

cartella = config['Watcher']['folder']
DELAY_RIAVVIO = int(config['Watcher']['delay_riavvio'])

OCR_BOX1 = tuple(map(int, config['OCR']['box1'].split(',')))
OCR_BOX2 = tuple(map(int, config['OCR']['box2'].split(',')))
OCR_SHOW_RECTS = config['OCR'].getboolean('show_rects')
OCR_LANG = config['OCR']['lang']
OCR_DPI = int(config['OCR']['dpi'])

FILENAME_MAX_LENGTH = int(config['Filename']['max_length'])
FILENAME_REMOVE_LEADING_ZEROS = config['Filename'].getboolean('remove_leading_zeros')

processati = {}

# ──────────────────────────────────────────────────────────────────────
# Inline rename logic (was main.py)
# ──────────────────────────────────────────────────────────────────────

def pulisci_nome(testo):
    clean = re.sub(r'[^\w\s.-]', '', testo).replace('\n', ' ').strip()
    if len(clean) > FILENAME_MAX_LENGTH:
        clean = clean[:FILENAME_MAX_LENGTH]
    if FILENAME_REMOVE_LEADING_ZEROS:
        clean = re.sub(r'^0+', '', clean)
    return clean


def rinomina_pdf(path_pdf):
    """
    Esegue OCR sulla prima pagina del PDF e rinomina il file
    con il contenuto estratto (numero documento + ragione sociale).
    """
    try:
        immagini = convert_from_path(path_pdf, dpi=OCR_DPI, first_page=1, last_page=1)
        img = immagini[0]

        if OCR_SHOW_RECTS:
            draw = ImageDraw.Draw(img)
            draw.rectangle(OCR_BOX1, outline="red", width=3)
            draw.rectangle(OCR_BOX2, outline="red", width=3)
            img.show()

        ritaglio1 = img.crop(OCR_BOX1)
        ritaglio2 = img.crop(OCR_BOX2)

        testo1 = pytesseract.image_to_string(ritaglio1, lang=OCR_LANG)
        testo2 = pytesseract.image_to_string(ritaglio2, lang=OCR_LANG)

        nuovo_nome_base = pulisci_nome(testo1) + " " + pulisci_nome(testo2)
        nuovo_nome_file = nuovo_nome_base + ".pdf"
        cartella_doc = os.path.dirname(path_pdf)
        nuovo_path = os.path.join(cartella_doc, nuovo_nome_file)

        if not os.path.exists(nuovo_path):
            os.rename(path_pdf, nuovo_path)
            print(f"✅ Rinominato '{os.path.basename(path_pdf)}' → '{nuovo_nome_file}'")
        else:
            counter = 1
            while os.path.exists(nuovo_path):
                nuovo_nome_file = f"{nuovo_nome_base} ({counter}).pdf"
                nuovo_path = os.path.join(cartella_doc, nuovo_nome_file)
                counter += 1
            os.rename(path_pdf, nuovo_path)
            print(f"✅ Rinominato '{os.path.basename(path_pdf)}' → '{nuovo_nome_file}' (conflitto risolto)")

    except Exception as e:
        print(f"❌ Errore OCR per '{os.path.basename(path_pdf)}': {e}")


# ──────────────────────────────────────────────────────────────────────
# Watcher logic
# ──────────────────────────────────────────────────────────────────────

def file_pronto(path):
    """Aspetta che il file finisca di essere scritto dalla fotocopiatrice"""
    try:
        size1 = os.path.getsize(path)
        time.sleep(1)
        size2 = os.path.getsize(path)
        return size1 == size2
    except:
        return False


def processa_file(path):
    if not path:
        return
    if not path.endswith(".pdf"):
        return
    if not os.path.basename(path).startswith("DOC"):
        return

    now = time.time()

    if path in processati:
        if now - processati[path] < DELAY_RIAVVIO:
            return

    processati[path] = now

    for _ in range(5):
        if os.path.exists(path) and file_pronto(path):
            break
        time.sleep(1)

    print(f"✔ File pronto: {path}")
    rinomina_pdf(path)


class MyHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            processa_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            processa_file(event.dest_path)

    def on_modified(self, event):
        if not event.is_directory:
            processa_file(event.src_path)


if __name__ == "__main__":
    # Process existing files on startup
    for filename in os.listdir(cartella):
        if filename.startswith("DOC") and filename.endswith(".pdf"):
            fullpath = os.path.join(cartella, filename)
            processa_file(fullpath)

    observer = Observer()
    event_handler = MyHandler()
    observer.schedule(event_handler, cartella, recursive=False)
    observer.start()

    print(f"\n👀 Monitoraggio attivo su: {cartella}")
    print("   Premi Ctrl+C per fermare.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()