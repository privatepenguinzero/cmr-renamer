import time
import os
import subprocess
import configparser
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Try to import tkinter for folder dialog, fallback to console if not available
try:
    from tkinter import Tk
    from tkinter.filedialog import askdirectory
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.ini')

def prompt_console(prompt_text, default=None):
    """Prompt user for input in console with optional default"""
    if default is not None:
        user_input = input(f"{prompt_text} [{default}]: ").strip()
        if user_input == '':
            return default
        return user_input
    else:
        return input(prompt_text).strip()

def prompt_for_folder():
    """Prompt user to select a folder using GUI if available, else console"""
    if TKINTER_AVAILABLE:
        try:
            # Create a root window and hide it
            root = Tk()
            root.withdraw()
            folder = askdirectory(title="Seleziona la cartella da monitorare")
            root.destroy()
            if folder:
                return folder
            # If user cancels, fall back to console
            print("Nessuna cartella selezionata. Inserisci il percorso manualmente.")
        except Exception as e:
            print(f"Errore nell'apertura della finestra di dialogo: {e}. Passando all'input console.")

    # Console fallback
    return prompt_console("Inserisci il percorso completo della cartella da monitorare")

def load_or_create_config():
    """Load configuration from config.ini, create it if missing by prompting user"""
    config = configparser.ConfigParser()

    if not os.path.exists(CONFIG_FILE):
        print("File di configurazione non trovato. Creazione guidata...")
        config = configparser.ConfigParser()

        # Watcher section
        config['Watcher'] = {}
        config['Watcher']['folder'] = prompt_for_folder()
        default_main_script = os.path.join(os.path.dirname(__file__), 'main.py')
        config['Watcher']['main_script'] = prompt_console(
            "Inserisci il percorso completo dello script di rinomina (main.py)",
            default_main_script
        )
        config['Watcher']['delay_riavvio'] = prompt_console(
            "Inserisci il delay di riavvio in secondi",
            "3"
        )

        # OCR section
        config['OCR'] = {}
        config['OCR']['box1'] = prompt_console(
            "Inserisci le coordinate box1 (x1,y1,x2,y2)",
            "595,1615,760,1750"
        )
        config['OCR']['box2'] = prompt_console(
            "Inserisci le coordinate box2 (x1,y1,x2,y2)",
            "230,720,1085,785"
        )
        show_rects_default = prompt_console(
            "Mostrare i rettangoli di estrazione durante il debug? (True/False)",
            "False"
        ).lower()
        config['OCR']['show_rects'] = show_rects_default in ['true', '1', 'yes', 'y']
        config['OCR']['lang'] = prompt_console(
            "Inserisci la lingua per OCR (es: eng, ita)",
            "eng"
        )
        config['OCR']['dpi'] = prompt_console(
            "Inserisci la DPI per la conversione PDF",
            "300"
        )

        # Filename section
        config['Filename'] = {}
        config['Filename']['max_length'] = prompt_console(
            "Inserisci la lunghezza massima per ogni parte del nome file",
            "60"
        )
        remove_zeros_default = prompt_console(
            "Rimuovere gli zeri iniziali dai numeri estratti? (True/False)",
            "True"
        ).lower()
        config['Filename']['remove_leading_zeros'] = remove_zeros_default in ['true', '1', 'yes', 'y']

        # Save the created config
        with open(CONFIG_FILE, 'w') as f:
            config.write(f)
        print(f"File di configurazione creato: {CONFIG_FILE}")
    else:
        config.read(CONFIG_FILE)
        print(f"File di configurazione caricato: {CONFIG_FILE}")

    return config

# Load configuration
config = load_or_create_config()

# Watcher settings
cartella = config['Watcher']['folder']
script_rinomina = config['Watcher']['main_script']
DELAY_RIAVVIO = int(config['Watcher']['delay_riavvio'])

# OCR settings (will be used in main.py via config)
OCR_BOX1 = tuple(map(int, config['OCR']['box1'].split(',')))
OCR_BOX2 = tuple(map(int, config['OCR']['box2'].split(',')))
OCR_SHOW_RECTS = config['OCR'].getboolean('show_rects')
OCR_LANG = config['OCR']['lang']
OCR_DPI = int(config['OCR']['dpi'])

# Filename settings
FILENAME_MAX_LENGTH = int(config['Filename']['max_length'])
FILENAME_REMOVE_LEADING_ZEROS = config['Filename'].getboolean('remove_leading_zeros')

processati = {}


def file_pronto(path):
    """
    Aspetta che il file finisca di essere scritto dalla fotocopiatrice
    """
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

    try:
        subprocess.run(["python", script_rinomina, path])
    except Exception as e:
        print(f"❌ Errore script: {e}")


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

    # processa file già presenti all'avvio
    for filename in os.listdir(cartella):
        if filename.startswith("DOC") and filename.endswith(".pdf"):
            fullpath = os.path.join(cartella, filename)
            processa_file(fullpath)

    observer = Observer()
    event_handler = MyHandler()
    observer.schedule(event_handler, cartella, recursive=False)
    observer.start()

    print(f"👀 Monitoraggio attivo su {cartella}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()