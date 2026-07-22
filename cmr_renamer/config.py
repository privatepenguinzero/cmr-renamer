"""Configuration management for CMR Renamer.

Handles loading and creating config.ini with guided prompts when missing.
"""

import os
import sys
import configparser

try:
    from tkinter import Tk
    from tkinter.filedialog import askdirectory
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


def _prompt_console(prompt_text, default=None):
    """Prompt user for input in console with optional default."""
    if default is not None:
        user_input = input(f"{prompt_text} [{default}]: ").strip()
        return user_input if user_input else default
    return input(prompt_text + ": ").strip()


def _prompt_for_folder():
    """Prompt user to select a folder using GUI if available, else console."""
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
            print(f"Errore apertura finestra: {e}. Input manuale.")

    return _prompt_console("Inserisci il percorso completo della cartella da monitorare")


def load_or_create_config(config_path: str = None) -> "configparser.ConfigParser":
    """Load config.ini — create it interactively if missing.

    Args:
        config_path: Optional path to config.ini. If not provided, the default
                     location is used (same directory as the executable if frozen,
                     otherwise the current working directory).

    Returns a configparser.ConfigParser instance ready to use.
    """
    if config_path is None:
        if getattr(sys, 'frozen', False):
            config_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            # When running from source, look for config.ini in the same directory
            # as the script being run (not necessarily this config.py file)
            if getattr(sys, 'argv', None) and len(sys.argv) > 0:
                script_path = os.path.abspath(sys.argv[0])
                if os.path.exists(script_path):
                    config_dir = os.path.dirname(script_path)
                else:
                    config_dir = os.getcwd()
            else:
                # Fallback to current working directory
                config_dir = os.getcwd()
        config_path = os.path.join(config_dir, 'config.ini')

    config = configparser.ConfigParser()

    if not os.path.exists(config_path):
        print("\n=== FILE DI CONFIGURAZIONE NON TROVATO ===")
        print(f"Percorso atteso: {config_path}")
        print("Creazione guidata — Invio per usare il valore predefinito tra [parentesi].\n")

        config['Watcher'] = {}
        config['Watcher']['folder'] = _prompt_for_folder()
        config['Watcher']['prefix'] = _prompt_console(
            "Prefisso del nome file da cercare (es. DOC)", "DOC"
        )
        config['Watcher']['delay_riavvio'] = _prompt_console(
            "Delay tra rilevamenti ripetuti (secondi)", "3"
        )

        config['OCR'] = {}
        config['OCR']['box1'] = _prompt_console(
            "Coordinate box1 — numero documento (x1,y1,x2,y2)", "595,1615,760,1750"
        )
        config['OCR']['box2'] = _prompt_console(
            "Coordinate box2 — ragione sociale (x1,y1,x2,y2)", "230,725,1085,805"
        )
        show_rects = _prompt_console(
            "Mostrare rettangoli di debug? (True/False)", "False"
        ).lower()
        config['OCR']['show_rects'] = str(show_rects in ['true', '1', 'yes', 'y'])
        config['OCR']['lang'] = _prompt_console("Lingua OCR (eng/ita/fra)", "eng")
        config['OCR']['dpi'] = _prompt_console("DPI per conversione PDF", "300")

        config['Filename'] = {}
        config['Filename']['max_length'] = _prompt_console(
            "Lunghezza massima per parte del nome file", "60"
        )
        remove_zeros = _prompt_console(
            "Rimuovere zeri iniziali dai numeri? (True/False)", "True"
        ).lower()
        config['Filename']['remove_leading_zeros'] = str(remove_zeros in ['true', '1', 'yes', 'y'])

        with open(config_path, 'w') as f:
            config.write(f)
        print(f"\n✅ Configurazione salvata in: {config_path}\n")
    else:
        config.read(config_path)
        print(f"📂 Configurazione caricata da: {config_path}")

    return config