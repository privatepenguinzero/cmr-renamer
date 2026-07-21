import sys
import os
import configparser
from pdf2image import convert_from_path
from PIL import Image, ImageDraw
import pytesseract
import re

# Load configuration from config.ini in the same directory as this script
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.ini')
config = configparser.ConfigParser()

if os.path.exists(CONFIG_FILE):
    config.read(CONFIG_FILE)
else:
    print(f"ATTENZIONE: File di configurazione {CONFIG_FILE} non trovato. Uso valori di default.")
    # Create default config sections if missing
    if not config.has_section('OCR'):
        config.add_section('OCR')
    if not config.has_section('Filename'):
        config.add_section('Filename')

    # Set defaults if config is missing
    if not config.has_option('OCR', 'box1'):
        config.set('OCR', 'box1', '595,1615,760,1750')
    if not config.has_option('OCR', 'box2'):
        config.set('OCR', 'box2', '230,720,1085,785')
    if not config.has_option('OCR', 'show_rects'):
        config.set('OCR', 'show_rects', 'False')
    if not config.has_option('OCR', 'lang'):
        config.set('OCR', 'lang', 'eng')
    if not config.has_option('OCR', 'dpi'):
        config.set('OCR', 'dpi', '300')
    if not config.has_option('Filename', 'max_length'):
        config.set('Filename', 'max_length', '60')
    if not config.has_option('Filename', 'remove_leading_zeros'):
        config.set('Filename', 'remove_leading_zeros', 'True')

# OCR settings from config
box1 = tuple(map(int, config['OCR']['box1'].split(',')))
box2 = tuple(map(int, config['OCR']['box2'].split(',')))
show_rects = config['OCR'].getboolean('show_rects')
ocr_lang = config['OCR']['lang']
ocr_dpi = int(config['OCR']['dpi'])

# Filename settings from config
max_length = int(config['Filename']['max_length'])
remove_leading_zeros = config['Filename'].getboolean('remove_leading_zeros')


def pulisci_nome(testo):
    # Remove special characters, keep alphanumeric, spaces, dots, hyphens
    clean = re.sub(r'[^\w\s.-]', '', testo).replace('\n', ' ').strip()

    # Limit length
    if len(clean) > max_length:
        clean = clean[:max_length]

    # Remove leading zeros if configured
    if remove_leading_zeros:
        clean = re.sub(r'^0+', '', clean)

    return clean


if len(sys.argv) < 2:
    print("Uso: main.py <path_file_pdf>")
    sys.exit(1)

path_pdf = sys.argv[1]
cartella = os.path.dirname(path_pdf)

try:
    immagini = convert_from_path(path_pdf, dpi=ocr_dpi, first_page=1, last_page=1)
    img = immagini[0]
##########################################################################
    # (FACOLTATIVO) Visualizza l'immagine con rettangoli rossi
    if show_rects:
        draw = ImageDraw.Draw(img)
        draw.rectangle(box1, outline="red", width=3)
        draw.rectangle(box2, outline="red", width=3)
        img.show()
##########################################################################

    ritaglio1 = img.crop(box1)
    ritaglio2 = img.crop(box2)

    testo_estratto1 = pytesseract.image_to_string(ritaglio1, lang=ocr_lang)
    testo_estratto2 = pytesseract.image_to_string(ritaglio2, lang=ocr_lang)

    nuovo_nome_base = pulisci_nome(testo_estratto1) + " " + pulisci_nome(testo_estratto2)
    nuovo_nome_file = nuovo_nome_base + ".pdf"
    nuovo_path = os.path.join(cartella, nuovo_nome_file)

    if not os.path.exists(nuovo_path):
        os.rename(path_pdf, nuovo_path)
        print(f"Rinominato '{os.path.basename(path_pdf)}' in '{nuovo_nome_file}'")
    else:
        # Handle filename collision by adding a counter
        counter = 1
        base_name_without_ext = nuovo_nome_base
        while os.path.exists(nuovo_path):
            nuovo_nome_file = f"{base_name_without_ext} ({counter}).pdf"
            nuovo_path = os.path.join(cartella, nuovo_nome_file)
            counter += 1
        os.rename(path_pdf, nuovo_path)
        print(f"Rinominato '{os.path.basename(path_pdf)}' in '{nuovo_nome_file}' (con risoluzione conflitto)")
except Exception as e:
    print(f"Errore elaborando '{os.path.basename(path_pdf)}': {e}")