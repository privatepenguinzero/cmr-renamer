"""Configuration management for CMR Renamer.

Handles loading and creating config.ini with guided prompts when missing.
"""

import os
import sys
import configparser

from . import gui

if gui.QT_AVAILABLE:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton,
    )


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
    """Unisce le lingue selezionate (checkbox) e il testo extra in una stringa tipo 'ita+deu+eng'.

    L'italiano va per primo: con più lingue il motore LSTM usa la prima come
    principale, e sul ritaglio reale di un CMR "ita" per prima ha letto meglio
    (confidenza 92 contro 88 su "ELEKTRONIKA").
    """
    codes = []
    if ita:
        codes.append('ita')
    if deu:
        codes.append('deu')
    if eng:
        codes.append('eng')
    extra_codes = [c.strip() for c in extra.split('+') if c.strip()]
    codes.extend(extra_codes)
    return '+'.join(codes)


def _prompt_for_folder():
    """Prompt user to select a folder using GUI if available, else console."""
    if gui.applicazione() is not None:
        try:
            folder = gui.scegli_cartella("Seleziona la cartella da monitorare")
            if folder:
                return folder
            print("Nessuna cartella selezionata. Inserisci il percorso manualmente.")
        except Exception as e:
            print(f"Errore apertura finestra: {e}. Input manuale.")

    return _prompt_console("Inserisci il percorso completo della cartella da monitorare")


def _prompt_with_gui() -> "dict | None":
    """Mostra un'unica finestra Qt con tutti i campi di configurazione iniziale.

    Ritorna un dict con le stesse chiavi che load_or_create_config scrive in config.ini (tutti
    valori stringa), oppure None se Qt non è disponibile o la finestra viene chiusa senza
    salvare — in quel caso il chiamante ricade sui prompt a console.
    """
    if gui.applicazione() is None:
        return None

    try:
        dialogo = QDialog()
        dialogo.setWindowTitle("CMR Renamer - Configurazione Iniziale")
        griglia = QGridLayout(dialogo)

        folder_edit = QLineEdit()
        folder_edit.setMinimumWidth(320)
        prefix_edit = QLineEdit("DOC")
        delay_edit = QLineEdit("3")
        dpi_edit = QLineEdit("300")
        max_length_edit = QLineEdit("60")
        extra_lang_edit = QLineEdit()
        # Tutte e tre sono incluse nell'exe. Il solo "eng" leggeva "d.o.o." come
        # "4.0.0." (confidenza 45) su un CMR reale, dove "ita" legge la "d" giusta.
        eng_check = QCheckBox("eng")
        ita_check = QCheckBox("ita")
        deu_check = QCheckBox("deu")
        remove_zeros_check = QCheckBox("Rimuovi zeri iniziali dai numeri")
        for check in (eng_check, ita_check, deu_check, remove_zeros_check):
            check.setChecked(True)

        def browse_folder():
            chosen = gui.scegli_cartella("Seleziona la cartella da monitorare", folder_edit.text())
            if chosen:
                folder_edit.setText(chosen)

        sfoglia = QPushButton("Sfoglia...")
        sfoglia.clicked.connect(browse_folder)

        lang_riga = QHBoxLayout()
        for check in (eng_check, ita_check, deu_check):
            lang_riga.addWidget(check)
        lang_riga.addStretch(1)

        righe = [
            ("Cartella da monitorare:", folder_edit),
            ("Prefisso file (es. DOC):", prefix_edit),
            ("Delay tra rilevamenti (secondi):", delay_edit),
            ("Lingua OCR:", lang_riga),
            ("Altre lingue (es. fra, separate da +):", extra_lang_edit),
            ("DPI per conversione PDF:", dpi_edit),
            ("Lunghezza massima per parte del nome file:", max_length_edit),
        ]
        for row, (testo, campo) in enumerate(righe):
            griglia.addWidget(QLabel(testo), row, 0)
            if isinstance(campo, QHBoxLayout):
                griglia.addLayout(campo, row, 1)
            else:
                griglia.addWidget(campo, row, 1)
        griglia.addWidget(sfoglia, 0, 2)
        row = len(righe)
        griglia.addWidget(remove_zeros_check, row, 0, 1, 3)
        row += 1

        error_label = QLabel("")
        error_label.setStyleSheet("color: #b00020;")
        griglia.addWidget(error_label, row, 0, 1, 3)
        row += 1

        pulsanti = QDialogButtonBox()
        conferma = pulsanti.addButton("Conferma", QDialogButtonBox.AcceptRole)
        conferma.setDefault(True)
        griglia.addWidget(pulsanti, row, 0, 1, 3, Qt.AlignCenter)

        result = {'value': None}

        def on_confirm():
            delay = _parse_positive_int(delay_edit.text())
            dpi = _parse_positive_int(dpi_edit.text())
            max_length = _parse_positive_int(max_length_edit.text())
            lang = _build_lang_string(eng_check.isChecked(), ita_check.isChecked(),
                                      deu_check.isChecked(), extra_lang_edit.text())

            if delay is None:
                error_label.setText("Il delay tra rilevamenti deve essere un numero intero positivo.")
                return
            if dpi is None:
                error_label.setText("Il DPI deve essere un numero intero positivo.")
                return
            if max_length is None:
                error_label.setText("La lunghezza massima del nome deve essere un numero intero positivo.")
                return
            if not lang:
                error_label.setText("Seleziona almeno una lingua OCR.")
                return

            result['value'] = {
                'folder': folder_edit.text(),
                'prefix': prefix_edit.text() or "DOC",
                'delay_riavvio': str(delay),
                'lang': lang,
                'dpi': str(dpi),
                'max_length': str(max_length),
                'remove_leading_zeros': str(remove_zeros_check.isChecked()),
            }
            dialogo.accept()

        conferma.clicked.connect(on_confirm)
        # Closing the window (or Esc) rejects: result stays None.
        dialogo.exec()
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
            config['OCR']['lang'] = _prompt_console("Lingua OCR (eng/ita/deu/fra)", "ita+deu+eng")
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