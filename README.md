# CMR Renamer

A file watcher and OCR-based renamer for CMR documents.

## Setup

1. **Install dependencies** using **uv** (recommended):
   ```bash
   # Install uv if you don't have it
   pip install uv
   # Sync dependencies from pyproject.toml
   uv sync
   ```

   This will install `watchdog`, `pillow`, `pytesseract`, and `pdf2image`.

2. **Configure**:
   - Run `python watcher.py`. If `config.ini` is missing, you will be prompted to select the folder to monitor and set OCR parameters. The configuration will be saved to `config.ini` for future runs.

## Running

```bash
python watcher.py
```

The watcher will monitor the configured folder for new PDF files prefixed with `DOC`, process them using OCR, and rename them according to the extracted text.

## Building a Windows Executable (GitHub Action)

A GitHub Actions workflow (`.github/workflows/build_release.yml`) is provided to build a single‑file Windows executable (`cmr-renamer.exe`) using **PyInstaller** and publish it as a release asset.

## Bundled third-party binaries

The built executable vendors two native tools so end users don't need to install anything
separately:

- **[Poppler](https://poppler.freedesktop.org/)** (GPL) — used by `pdf2image` to rasterize PDF
  pages. The compiled binaries are redistributed as-is and invoked as external processes (not
  linked into this project's code); license text is included at `vendor/poppler/COPYING` and
  `vendor/poppler/COPYING.gpl2`.
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** (Apache License 2.0) — used by
  `pytesseract` to perform OCR. License text is included at `vendor/tesseract/LICENSE`. Only
  `eng`/`ita`/`osd` language data is bundled; other OCR languages require a system-installed
  Tesseract with that language's data.

## License

MIT License
