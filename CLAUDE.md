# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
CMR Renamer is a file watcher and OCR-based renamer for CMR documents. It monitors a directory for new PDF files prefixed with "DOC", processes them using OCR to extract text, and renames the files based on the extracted text.

## Development Setup

### Dependencies
Dependencies are managed with `uv`. Install dependencies with:
```bash
uv sync
```

### Running the Application
```bash
python watcher.py
```

### Running as Module
```bash
python -m cmr_renamer
```

### Installation (Alternative)
```bash
pip install -e .
```

Then run with:
```bash
cmr-renamer
```

### Testing
Run tests with pytest (if tests exist in test directory):
```bash
pytest
```

Or run individual test files:
```bash
python -m pytest test_folder/
```

## Project Structure

```
cmr-renamer/
├── cmr_renamer/              # Main package
│   ├── __init__.py
│   ├── __main__.py          # Entry point for module execution
│   ├── cli.py               # CLI entry point (used in pyproject.toml)
│   ├── config.py            # Configuration handling
│   └── watcher.py           # Main file watching and OCR logic
├── main.py                  # Root entry point (used by PyInstaller)
├── config.ini               # Configuration file (created on first run)
├── config.ini.bak           # Backup configuration template
├── pyproject.toml           # Project configuration and dependencies
├── README.md                # Project documentation
└── test_folder/             # Test directory for PDF files
```

### Key Components

#### watcher.py (cmr_renamer/watcher.py)
- **Main Application Logic**: Contains the core file watching and OCR processing logic
- **Key Components**:
  - `CMRHandler`: FileSystemEventHandler subclass that processes PDF files
  - `_rinomina_pdf`: Performs OCR on PDF and renames file based on extracted text
  - `_pulisci_nome`: Cleans OCR text for use as filename
  - `_file_pronto`: Waits for file to finish writing (copier completion)
  - Console management functions for Windows executable mode
  - Configuration loading and parsing

#### Configuration (config.py)
Handles loading and creating configuration from `config.ini`:
- **Watcher section**: Folder to monitor and delay settings
- **OCR section**: OCR regions (box1, box2), language, DPI, and visualization options
- **Filename section**: Filename constraints (max length, zero removal)

#### Entry Points
- `main.py`: Root entry point used by PyInstaller builds
- `cmr_renamer.cli:run`: CLI entry point defined in pyproject.toml
- `cmr_renamer.watcher.run`: Direct module execution

## Development Guidelines

### Adding Features
1. Modify `watcher.py` for core functionality changes
2. Update `config.py` if new configuration options are needed
3. Update `config.ini.bak` template if adding new config options
4. Update `pyproject.toml` if adding new dependencies

### Windows Executable Build
The project uses GitHub Actions (`.github/workflows/build_release.yml`) to build Windows executables with PyInstaller. The build process:
- Uses PyInstaller with `--windowed` flag for GUI-less execution
- Creates a single-file executable (`cmr-renamer.exe`)
- Includes all necessary dependencies

### Configuration First Run
On first execution (or when `config.ini` is missing):
1. Application prompts for folder to monitor
2. Asks for OCR configuration (regions, language, DPI)
3. Optionally shows OCR rectangles for visual confirmation
4. Saves configuration to `config.ini` for future runs

### File Processing Flow
1. Watcher monitors configured directory for new/modified PDF files starting with "DOC"
2. When detected, waits for file to finish copying/writing
3. Converts first page of PDF to image
4. Applies OCR to two defined regions (box1 and box2)
5. Cleans extracted text (removes special characters, limits length, optionally removes leading zeros)
6. Renames file to `{cleaned_text1} {cleaned_text2}.pdf`
7. Handles filename conflicts by adding counter suffix

## Common Commands

### Development
```bash
# Install dependencies
uv sync

# Run the application
python watcher.py

# Run as module
python -m cmr_renamer

# Run tests (if available)
pytest
```

### Packaging
```bash
# Build executable manually (alternative to GitHub Actions)
pyinstaller --onefile --windowed main.py
```

## Configuration File Format
See `config.ini.bak` for template. Key sections:
- `[Watcher]`: folder, delay_riavvio
- `[OCR]`: box1, box2, show_rects, lang, dpi
- `[Filename]`: max_length, remove_leading_zeros

## Troubleshooting
- If OCR fails to recognize text, adjust box coordinates in config.ini
- Increase DPI in config.ini for better OCR accuracy on low-quality scans
- Ensure Tesseract OCR is installed and accessible in PATH
- For PDF processing issues, ensure poppler is installed (for pdf2image)

## License
MIT License - See LICENSE file for details.