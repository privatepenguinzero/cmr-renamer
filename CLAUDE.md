# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CMR Renamer watches a folder for new PDF files whose name starts with `DOC`, runs OCR on two fixed
regions of the first page (document number + company name), and renames the file to the extracted
text. It's a Windows-targeted background tool (built into a `.exe` via PyInstaller) but runs fine
from source on any platform with Tesseract installed.

## Commands

```bash
# Install dependencies (uv is the supported tool; see pyproject.toml)
uv sync

# Run from source
python main.py               # same entry point PyInstaller builds from
python -m cmr_renamer         # equivalent, via __main__.py
uv run cmr-renamer            # equivalent, via the installed console script

# Build the Windows executable locally (mirrors .github/workflows/build_release.yml)
uv run -- pyinstaller --onefile --windowed --name cmr-renamer main.py
```

There is no test suite, linter, or formatter configured in this repo — don't assume `pytest`/`ruff`
exist. Tesseract OCR must be installed on the system (`pytesseract` only wraps the binary); the CI
workflow installs it via `choco install tesseract` on windows-latest.

## Architecture

**Three converging entry points, one implementation.** `main.py` (repo root), `cmr_renamer/__main__.py`,
and `cmr_renamer/cli.py` (the `cmr-renamer` console script target) all just call
`cmr_renamer.watcher.run()`. `main.py` exists specifically so PyInstaller can build with absolute
imports — building from inside the package triggers a "relative import with no known parent package"
error, which is why the GitHub Actions workflow builds from `main.py`, not from the package. Keep new
top-level behavior in `watcher.run()` so all three entry points stay in sync automatically.

**`watcher.py` is the whole application**: config loading, OCR, filename cleaning, and the watchdog
event handler (`CMRHandler`) all live here. Frozen-vs-source and interactive-vs-background execution
are all branched inside `run()`:
- Not frozen (running from source): behaves like a normal console script.
- Frozen + no `config.ini`: allocates a Windows console (`_alloc_console`/`_free_console`, via
  `ctypes.windll.kernel32`) so the interactive setup prompts are visible, then frees it and switches
  to file logging (`_setup_file_logging` redirects `sys.stdout`/`stderr` to `cmr-renamer.log`) for
  normal background operation.
- Frozen + config exists: goes straight to background/file-logging mode — no console at all, since
  the exe is built with `--windowed`.

**Config lives next to the executable (or CWD when running from source)**, in `config.ini`, and is
created interactively on first run by `config.py` (`load_or_create_config`). It prefers a tkinter
folder picker for the watched directory, falling back to console input if tkinter/GUI is unavailable.
Config sections: `[Watcher]` (folder, delay_riavvio), `[OCR]` (box1/box2 crop coordinates, show_rects
debug flag, lang, dpi), `[Filename]` (max_length, remove_leading_zeros). `watcher.run()` reads and
type-converts every value out of the raw `ConfigParser` into plain dicts (`ocr_cfg`, `name_cfg`)
before using them — if you add a config key, update both `config.py`'s prompts and this parsing step.

**Processing pipeline** (`_rinomina_pdf`): `pdf2image.convert_from_path` renders page 1 → `PIL` crops
the two configured boxes → `pytesseract.image_to_string` OCRs each crop → `_pulisci_nome` strips
non-word characters, truncates to `max_length`, optionally strips leading zeros → the two cleaned
strings are joined into the new filename, with `(1)`, `(2)`, ... appended on collision.

**Watching**: `CMRHandler` (a `watchdog` `FileSystemEventHandler`) reacts to created/moved/modified
events, filters to `*.pdf` files starting with `DOC`, waits for the file to stop growing
(`_file_pronto`, since files typically arrive from a scanner/copier still being written), then calls
`_rinomina_pdf`. A `processati` dict debounces repeat events per path using `delay_riavvio` from
config. `run()` also sweeps the watched folder for pre-existing matching files once at startup, before
starting the observer loop.

**Language note**: user-facing console strings and internal helper names (`_rinomina_pdf`,
`_pulisci_nome`, `_file_pronto`) are Italian (the tool's target users); docstrings/comments are
English. Match the existing convention for the kind of string/identifier you're touching.
