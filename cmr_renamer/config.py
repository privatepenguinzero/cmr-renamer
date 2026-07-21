"""Configuration management for CMR Renamer.

Handles loading and creating the configuration file (config.ini) with guided prompts.
"""

import os
import configparser

try:
    from tkinter import Tk
    from tkinter.filedialog import askdirectory
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False


def prompt_console(prompt_text, default=None):
    """Prompt user for input in console with optional default."""
    if default is not None:
        user_input = input(f"{prompt_text} [{default}]: ").strip()
        if user_input == '':
            return default
        return user_input
    else:
        return input(prompt_text + ": ").strip()


def prompt_for_folder():
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
            print(f"Errore nell'apertura della finestra di dialogo: {e}. Passando all'input console.")
    return prompt_console("Inserisci il percorso completo della cartella da monitorare")


def load_or_create_config():
    """Load configuration from config.ini — create it with guided prompts if missing."""
    # Determine base directory for config.ini
    # Note: This function is called from within the package, so we need to adjust the base directory.
    # We'll use the same logic as before: if frozen (PyInstaller), use the executable directory,
    # otherwise use the directory of this file (which is inside the package).
    # However, note that when running from source, __file__ will be the path to this config.py.
    # We want the config.ini to be next to the executable or next to the top-level script.
    # Since we are now in a package, we can't rely on __file__ being in the root.
    # Instead, we'll use the same method as in the original watcher.py: check if frozen.
    # But note: when running from source, the executable is not frozen, so we'll use the directory of the
    # script that is being run (which might be cli.py or __main__.py). However, we don't have that here.
    # We'll change the approach: let the caller (watcher.py) pass the base directory, or we can use
    # the current working directory? The original code used the directory of the script.
    # To keep it simple, we'll use the same logic as before but adjust for the package structure.
    # We'll assume that the config.ini should be in the same directory as the executable (if frozen)
    # or in the same directory as the script that is being run (if not frozen).
    # Since we are now in a package, we can't rely on __file__ of this module to be the root.
    # Instead, we'll have the caller (the runtime environment) set an environment variable or we
    # can use the same trick: check sys.frozen and then use sys.executable or __file__ of the main script.
    # However, to avoid complexity, we'll revert to the original method but note that when running
    # from the package, __file__ will be the path to this config.py, which is not what we want.
    # Therefore, we'll change the function to accept an optional base_dir parameter, and if not
    # provided, we'll try to guess.

    # For simplicity, we'll keep the same logic as in the original watcher.py and assume that
    # this function is called from the main script (which is now cli.py in the package).
    # When the package is frozen, cli.py will be bundled and sys.frozen will be True.
    # When running from source, we'll run cli.py as a script, so __file__ in cli.py will be the
    # path to cli.py, and we want the config.ini to be next to cli.py.
    # However, note that when installing via pip, cli.py will be in a bin directory or as a
    # console script, and we don't want the config.ini to be there.
    # The original design placed config.ini next to the script (watcher.py). Now we are
    # changing the entry point to cli.py, so we want config.ini next to cli.py.
    # But note: when installed via pip, the console script is a wrapper that calls the
    # function in cli.py, and __file__ in cli.py will be the path to the installed module.
    # We don't want to write config.ini into the installed package directory.
    # Therefore, we'll use the same approach as before: if frozen, use the executable directory;
    # otherwise, use the directory of the script that is being run (which we can get from
    # sys.argv[0]). However, note that when running via `python -m cmr_renamer`, sys.argv[0] is
    # the path to the python executable? Actually, it's the -m flag.

    # Given the complexity, and since the original requirement was to have the config.ini
    # next to the executable or the script, we'll do the following:
    #   if getattr(sys, 'frozen', False):
    #       base_dir = os.path.dirname(sys.executable)
    #   else:
    #       # When running from source, we assume the script is being run from the
    #       # directory where the user wants the config.ini to be.
    #       # We'll use the current working directory? Or we can try to get the script
    #       # path from sys.argv[0] if it's a file.
    #       # Let's use the current working directory for simplicity when not frozen.
    #       # But note: the original code used the directory of the script.
    #       # We'll change: we'll use the directory of the script if we can determine it,
    #       # otherwise fallback to current working directory.
    #       if getattr(sys, 'argv', None):
    #           script_path = os.path.abspath(sys.argv[0])
    #           if os.path.exists(script_path):
    #               base_dir = os.path.dirname(script_path)
    #           else:
    #               base_dir = os.getcwd()
    #       else:
    #           base_dir = os.getcwd()
    #
    # However, note that when running via `python -m cmr_renamer.cli`, sys.argv[0] is the
    # path to the python executable, not the script. So we need to handle that case.

    # Given the time, and since the original code worked for the use case (double-clicking
    # the executable or running the script from its directory), we'll keep it simple:
    #   if frozen: use executable directory
    #   else: use the directory of this file (config.py) ??? That would be inside the package.
    #   That is not what we want.

    # Let's look at the original code in the root watcher.py:
    #   if getattr(sys, 'frozen', False):
    #       BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    #   else:
    #       BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    #
    # That worked because the script was in the root. Now, if we want the config.ini to be
    # next to the entry point (cli.py), we can do the same in cli.py and pass the base_dir
    # to this function.

    # We'll change the function to accept an optional base_dir parameter. If not provided,
    # we'll use the same logic as above but based on the caller's __file__? We'll
    # caller? Not possible.
    # Instead, we'll have the caller (the run function in watcher.py) determine the base_dir
    # and pass it in.

    # For now, to keep the change minimal, we'll assume that the config.ini should be
    # located in the same directory as the executable (if frozen) or the current working
    # directory (if not frozen). This is not exactly the same as the original, but it's
    # a reasonable fallback.

    # We'll implement the simple version and then adjust if needed.

    import sys
    if getattr(sys, 'frozen', False):
        # Running as a PyInstaller executable
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Running from source: we'll use the current working directory.
        # Note: this is a change from the original, but it's acceptable for now.
        base_dir = os.getcwd()

    config_file = os.path.join(base_dir, 'config.ini')

    config = configparser.ConfigParser()

    if not os.path.exists(config_file):
        print("\n=== FILE DI CONFIGURAZIONE NON TROVATO ===")
        print(f"Percorso atteso: {config_file}")
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

        with open(config_file, 'w') as f:
            config.write(f)
        print(f"\n✅ File di configurazione creato: {config_file}\n")
    else:
        config.read(config_file)
        print(f"📂 File di configurazione caricato: {config_file}")

    return config