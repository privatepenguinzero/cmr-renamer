import time
import os
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

cartella = r"<path main.py>"
script_rinomina = r"<path folder>"  # Path dello script di rinomina

processati = {}
DELAY_RIAVVIO = 3  # secondi

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
        subprocess.run(["pythonw", script_rinomina, path])
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
