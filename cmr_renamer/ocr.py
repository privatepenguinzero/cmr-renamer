"""OCR-related helpers for CMR Renamer.

Contains constants for OCR regions and functions to extract text from images.
"""

# These will be overridden by values from config.py at runtime
# Default values matching the original hardcoded values
OCR_BOX1 = (595, 1615, 760, 1750)  # numero documento
OCR_BOX2 = (230, 720, 1085, 785)   # ragione sociale
OCR_SHOW_RECTS = False
OCR_LANG = 'eng'
OCR_DPI = 300


def set_ocr_config(box1, box2, show_rects, lang, dpi):
    """Update OCR configuration values (called from config loading)."""
    global OCR_BOX1, OCR_BOX2, OCR_SHOW_RECTS, OCR_LANG, OCR_DPI
    OCR_BOX1 = box1
    OCR_BOX2 = box2
    OCR_SHOW_RECTS = show_rects
    OCR_LANG = lang
    OCR_DPI = dpi