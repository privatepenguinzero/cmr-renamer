"""Configuration management for CMR Renamer.

Handles loading and creating config.ini with guided prompts when missing.
"""

import os
import sys
import configparser

try:
    from tkinter import Tk, Frame, Label, Entry, Button, Checkbutton, BooleanVar, StringVar
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


def _parse_positive_int(value: str) -> "int | None":
    """Converte una stringa in intero positivo, o None se non valida."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _build_lang_string(eng: bool, ita: bool, deu: bool, extra: str) -> str:
    """Unisce le lingue selezionate (checkbox) e il testo extra in una stringa tipo 'eng+ita'."""
    codes = []
    if eng:
        codes.append('eng')
    if ita:
        codes.append('ita')
    if deu:
        codes.append('deu')
    extra_codes = [c.strip() for c in extra.split('+') if c.strip()]
    codes.extend(extra_codes)
    return '+'.join(codes)


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


def _prompt_with_gui() -> "dict | None":
    """Mostra un'unica finestra Tkinter con tutti i campi di configurazione iniziale.

    Ritorna un dict con le stesse chiavi che load_or_create_config scrive in config.ini (tutti
    valori stringa), oppure None se tkinter non è disponibile o la finestra viene chiusa senza
    salvare — in quel caso il chiamante ricade sui prompt a console.
    """
    if not TKINTER_AVAILABLE:
        return None

    result = {'value': None}

    try:
        root = Tk()
        root.title("CMR Renamer - Configurazione Iniziale")

        folder_var = StringVar(value="")
        prefix_var = StringVar(value="DOC")
        delay_var = StringVar(value="3")
        dpi_var = StringVar(value="300")
        max_length_var = StringVar(value="60")
        extra_lang_var = StringVar(value="")
        eng_var = BooleanVar(value=True)
        ita_var = BooleanVar(value=False)
        deu_var = BooleanVar(value=False)
        remove_zeros_var = BooleanVar(value=True)

        def browse_folder():
            chosen = askdirectory(title="Seleziona la cartella da monitorare")
            if chosen:
                folder_var.set(chosen)

        row = 0
        Label(root, text="Cartella da monitorare:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=folder_var, width=40).grid(row=row, column=1, padx=8, pady=4)
        Button(root, text="Sfoglia...", command=browse_folder).grid(row=row, column=2, padx=8, pady=4)
        row += 1

        Label(root, text="Prefisso file (es. DOC):").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=prefix_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="Delay tra rilevamenti (secondi):").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=delay_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="Lingua OCR:").grid(row=row, column=0, sticky="nw", padx=8, pady=4)
        lang_frame = Frame(root)
        lang_frame.grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        Checkbutton(lang_frame, text="eng", variable=eng_var).pack(side="left")
        Checkbutton(lang_frame, text="ita", variable=ita_var).pack(side="left")
        Checkbutton(lang_frame, text="deu", variable=deu_var).pack(side="left")
        row += 1

        Label(root, text="Altre lingue (es. fra, separate da +):").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=extra_lang_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="DPI per conversione PDF:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=dpi_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="Lunghezza massima per parte del nome file:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=max_length_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Checkbutton(root, text="Rimuovi zeri iniziali dai numeri", variable=remove_zeros_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=4
        )
        row += 1

        error_label = Label(root, text="", fg="red")
        error_label.grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        row += 1

        def on_confirm():
            delay = _parse_positive_int(delay_var.get())
            dpi = _parse_positive_int(dpi_var.get())
            max_length = _parse_positive_int(max_length_var.get())
            lang = _build_lang_string(eng_var.get(), ita_var.get(), deu_var.get(), extra_lang_var.get())

            if delay is None:
                error_label.config(text="Il delay tra rilevamenti deve essere un numero intero positivo.")
                return
            if dpi is None:
                error_label.config(text="Il DPI deve essere un numero intero positivo.")
                return
            if max_length is None:
                error_label.config(text="La lunghezza massima del nome deve essere un numero intero positivo.")
                return
            if not lang:
                error_label.config(text="Seleziona almeno una lingua OCR.")
                return

            result['value'] = {
                'folder': folder_var.get(),
                'prefix': prefix_var.get() or "DOC",
                'delay_riavvio': str(delay),
                'lang': lang,
                'dpi': str(dpi),
                'max_length': str(max_length),
                'remove_leading_zeros': str(remove_zeros_var.get()),
            }
            root.destroy()

        def on_close():
            result['value'] = None
            root.destroy()

        Button(root, text="Conferma", command=on_confirm).grid(row=row, column=0, columnspan=3, pady=8)
        root.protocol("WM_DELETE_WINDOW", on_close)

        root.mainloop()
        return result['value']
    except Exception as e:
        print(f"⚠️ Errore nella finestra di configurazione: {e}. Uso i prompt da console.")
        return None


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

        gui_values = _prompt_with_gui()

        if gui_values is not None:
            config['Watcher'] = {}
            config['Watcher']['folder'] = gui_values['folder']
            config['Watcher']['prefix'] = gui_values['prefix']
            config['Watcher']['delay_riavvio'] = gui_values['delay_riavvio']

            config['OCR'] = {}
            config['OCR']['lang'] = gui_values['lang']
            config['OCR']['dpi'] = gui_values['dpi']

            config['Filename'] = {}
            config['Filename']['max_length'] = gui_values['max_length']
            config['Filename']['remove_leading_zeros'] = gui_values['remove_leading_zeros']
        else:
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
            config['OCR']['lang'] = _prompt_console("Lingua OCR (eng/ita/deu/fra)", "eng")
            config['OCR']['dpi'] = _prompt_console("DPI per conversione PDF", "300")
            # box1..box5 are intentionally not asked here: they have no sensible
            # generic default and are selected with the mouse on the first PDF
            # that gets processed (see watcher._rinomina_pdf / _calibra_box).

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