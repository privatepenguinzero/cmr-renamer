"""Qt infrastructure shared by the setup form, the calibrator and the tray.

Qt allows widgets only on the thread that created the QApplication — the main
thread — while this program does its work on watchdog/updater threads. Every
GUI entry point used from those threads therefore goes through
`esegui_in_gui`, which runs the call on the main thread and waits for it.
The main thread must be running the Qt event loop (`ciclo_eventi`) for that to
work; if Qt is unavailable everything here degrades to "no GUI".
"""

import os
import sys
import threading

# Broad guard, not just ImportError: a missing or broken Qt
# install must leave the program without a GUI, not crash it.
try:
    from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot, qVersion
    from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QStyleFactory
    QT_AVAILABLE = True
except Exception:
    QT_AVAILABLE = False

_app = None
_ponte = None
_chiuso = threading.Event()


def _display_presente() -> bool:
    """False on a Linux box without a display, where Qt would abort the process.

    QApplication does not raise when it cannot reach a display: it calls
    qFatal() and kills the interpreter. Windows and macOS always have one.
    """
    if not sys.platform.startswith('linux'):
        return True
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')
                or os.environ.get('QT_QPA_PLATFORM'))


if QT_AVAILABLE:
    class _Ponte(QObject):
        """Lives on the main thread; queued signals land its slot there."""

        richiesta = Signal(object)

        def __init__(self):
            super().__init__()
            self.richiesta.connect(self._esegui, Qt.QueuedConnection)

        @Slot(object)
        def _esegui(self, lavoro):
            fn, args, kwargs, esito, fatto = lavoro
            try:
                esito['valore'] = fn(*args, **kwargs)
            except Exception as e:
                esito['errore'] = e
            finally:
                fatto.set()


def puo_avviarsi() -> bool:
    """Whether `applicazione()` can be expected to succeed, without creating it."""
    return QT_AVAILABLE and _display_presente()


def applicazione():
    """Return the QApplication, creating it on first use; None if there is no GUI.

    Must first be called from the main thread.
    """
    global _app, _ponte
    if not puo_avviarsi():
        return None
    if _app is None:
        try:
            _app = QApplication.instance() or QApplication(sys.argv[:1])
            # The tray app has no main window: closing the calibrator or a
            # message box must not end the event loop.
            _app.setQuitOnLastWindowClosed(False)
            _scegli_stile(_app)
            _ponte = _Ponte()
        except Exception as e:
            print(f"⚠️ Interfaccia Qt non disponibile: {e}")
            _app = None
    return _app


# Native Windows looks, provided by plugins/styles/qmodernwindowsstyle.dll.
STILI_NATIVI = ('windows11', 'windowsvista')


def _scegli_stile(app) -> None:
    """Avoid Qt's built-in "Windows" style, a Windows 95 look easily mistaken for Tk.

    Qt picks the native style by itself when its plugin loads; if the plugin is
    missing or fails to load, the fallback is that classic style. Fusion is
    built in and looks current, so it is the better second choice.
    """
    disponibili = {k.lower() for k in QStyleFactory.keys()}
    if sys.platform == 'win32' and not disponibili & set(STILI_NATIVI):
        app.setStyle('Fusion')


def descrizione_stile() -> str:
    """Qt version, active style and available styles, for the startup log."""
    if _app is None:
        return "interfaccia grafica non disponibile"
    return (f"Qt {qVersion()}, stile '{_app.style().name()}' "
            f"(disponibili: {', '.join(QStyleFactory.keys())})")


def campione_colore(colore: str, lato: int = 12) -> "QIcon":
    """Small filled square, to tag a native widget with a color without a stylesheet.

    A stylesheet on a button replaces the native style for it entirely, which
    is what made the calibrator's box buttons look flat, like Tk.
    """
    pixmap = QPixmap(lato, lato)
    pixmap.fill(QColor(colore))
    return QIcon(pixmap)


def disponibile() -> bool:
    """True when windows can be shown (Qt installed, display present, app created)."""
    return _app is not None and not _chiuso.is_set()


def esegui_in_gui(fn, *args, **kwargs):
    """Run `fn` on the GUI thread and return its result (re-raising its exception).

    Called on the main thread it simply runs `fn`. From another thread it waits
    for the event loop; if the loop has already ended it gives up and returns
    None instead of blocking a worker (and the shutdown that joins it) forever.
    """
    if _app is None:
        return None
    if threading.current_thread() is threading.main_thread():
        return fn(*args, **kwargs)
    esito, fatto = {}, threading.Event()
    _ponte.richiesta.emit((fn, args, kwargs, esito, fatto))
    while not fatto.wait(0.5):
        if _chiuso.is_set():
            return None
    if 'errore' in esito:
        raise esito['errore']
    return esito.get('valore')


def ciclo_eventi(stop_event: threading.Event) -> None:
    """Run the Qt event loop on the main thread until `stop_event` is set.

    A short timer polls the event, which also gives Python a chance to run its
    SIGINT handler: while Qt's C++ loop runs, Ctrl+C would otherwise wait.
    """
    timer = QTimer()
    timer.timeout.connect(lambda: stop_event.is_set() and _app.quit())
    timer.start(200)
    try:
        _app.exec()
    finally:
        timer.stop()
        _chiuso.set()


def da_pil(img) -> "QPixmap":
    """Convert a PIL image to a QPixmap without Qt's image-format plugins.

    Going through raw RGBA bytes keeps the .ico/.png plugins out of the build:
    PIL already decodes everything this program displays.
    """
    rgba = img.convert('RGBA')
    dati = rgba.tobytes('raw', 'RGBA')
    qimg = QImage(dati, rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888)
    # copy(): QImage does not own `dati`, which Python frees after return.
    return QPixmap.fromImage(qimg.copy())


def icona(img) -> "QIcon":
    """QIcon from a PIL image (e.g. the bundled .ico)."""
    return QIcon(da_pil(img))


def _finestra_messaggio(titolo: str, testo: str, domanda: bool) -> bool:
    box = QMessageBox()
    box.setWindowTitle(titolo)
    box.setText(testo)
    box.setWindowFlag(Qt.WindowStaysOnTopHint)
    if domanda:
        box.setIcon(QMessageBox.Question)
        # No Qt translations are bundled: Italian labels are set by hand.
        si = box.addButton("Sì", QMessageBox.YesRole)
        no = box.addButton("No", QMessageBox.NoRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is si
    box.setIcon(QMessageBox.Information)
    box.addButton("OK", QMessageBox.AcceptRole)
    box.exec()
    return True


def mostra_info(titolo: str, testo: str) -> None:
    """Information box, from any thread. No-op without a GUI."""
    if disponibile():
        esegui_in_gui(_finestra_messaggio, titolo, testo, False)


def chiedi_conferma(titolo: str, testo: str) -> "bool | None":
    """Yes/No box, from any thread. None when it cannot be shown."""
    if not disponibile():
        return None
    return esegui_in_gui(_finestra_messaggio, titolo, testo, True)


def scegli_cartella(titolo: str, iniziale: str = "") -> str:
    """Folder picker; empty string if cancelled or without a GUI."""
    if not disponibile():
        return ""
    return esegui_in_gui(QFileDialog.getExistingDirectory, None, titolo, iniziale) or ""
