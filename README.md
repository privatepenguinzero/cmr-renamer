# CMR Renamer

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)

A background file watcher that OCRs and renames CMR documents automatically. Drop a scanned PDF
into a watched folder and CMR Renamer reads the document number and company name straight off the
page, then renames the file for you — no manual typing, no manual filing.

## Features

- **Watches a folder** for new PDFs matching a configurable filename prefix and renames them from
  OCR'd text, with automatic `(1)`, `(2)`, … suffixes on name collisions
- **Interactive mouse calibrator** to draw the crop boxes OCR reads from — supports 2 to 5 boxes,
  so a filename can be built from more than just "document number + company name"
- **System tray icon** in background mode, with quick actions to open the log, open the watched
  folder, re-run the calibrator, or exit
- **Self-contained on Windows** — Poppler and Tesseract are bundled into the executable (see
  below), so there's nothing extra to install on the machine that runs it
- Rotating log file, OCR image preprocessing, and a guided first-run setup wizard

## Setup

1. **Install dependencies** using [**uv**](https://docs.astral.sh/uv/) (recommended):
   ```bash
   # Install uv if you don't have it
   pip install uv
   # Sync dependencies from pyproject.toml
   uv sync
   ```

   This installs `watchdog`, `pillow`, `pytesseract`, `pdf2image`, and `pystray`.

2. **Configure**:
   - Run `python main.py`. If `config.ini` is missing, you'll be prompted to select the folder to
     monitor and set OCR parameters. The configuration is saved to `config.ini` for future runs.

## Running

```bash
python main.py
# or, equivalently:
python -m cmr_renamer
uv run cmr-renamer
```

The watcher monitors the configured folder for new PDF files matching the configured prefix
(`DOC` by default), processes them with OCR, and renames them from the extracted text.

## Building a Windows Executable (GitHub Action)

A GitHub Actions workflow (`.github/workflows/build_release.yml`) builds a single-file Windows
executable (`cmr-renamer.exe`) with **PyInstaller** and publishes it as a release asset whenever a
release is published.

## Bundled third-party binaries

The built executable vendors two native tools so end users don't need to install anything
separately:

- **[Poppler](https://poppler.freedesktop.org/)** (GPL) — used by `pdf2image` to rasterize PDF
  pages. The compiled binaries are redistributed as-is and invoked as external processes (not
  linked into this project's code); license text is included at `vendor/poppler/COPYING` and
  `vendor/poppler/COPYING.gpl2`.
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** (Apache License 2.0) — used by
  `pytesseract` to perform OCR. License text is included at `vendor/tesseract/LICENSE`. Only
  `eng`/`ita`/`deu`/`osd` language data is bundled; other OCR languages require a system-installed
  Tesseract with that language's data.

## Acknowledgements

CMR Renamer stands on the work of some genuinely excellent open-source projects — thank you to
everyone who builds and maintains them:

- **[Poppler](https://poppler.freedesktop.org/)** and its contributors, for the PDF rendering
  engine this tool relies on to turn scanned pages into images — and to
  [**@oschwartz10612**](https://github.com/oschwartz10612/poppler-windows) for maintaining the
  portable Windows builds vendored here.
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** and the
  [tesseract-ocr](https://github.com/tesseract-ocr) organization, for the OCR engine that does the
  actual reading — and to [**UB-Mannheim**](https://github.com/UB-Mannheim/tesseract) for building
  and publishing the Windows installers this project's bundle is built from.
- The maintainers of [watchdog](https://github.com/gorakhargosh/watchdog), [Pillow](https://python-pillow.org/),
  [pytesseract](https://github.com/madmaze/pytesseract), [pdf2image](https://github.com/Belval/pdf2image),
  and [pystray](https://github.com/moses-palmer/pystray) — the Python ecosystem this tool is built
  entirely out of.

## License

MIT License
