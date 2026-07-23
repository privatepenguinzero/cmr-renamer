# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CMR Renamer watches a folder for new PDF files whose name starts with a configurable prefix (`DOC` by
default), runs OCR on two fixed regions of the first page (document number + company name), and
renames the file to the extracted text. It's a Windows-targeted background tool (built into a `.exe`
via PyInstaller) but runs fine from source on any platform with Tesseract installed.

## Commands

```bash
# Install dependencies (uv is the supported tool; see pyproject.toml)
uv sync

# Run from source
python main.py               # same entry point PyInstaller builds from
python -m cmr_renamer         # equivalent, via __main__.py
uv run cmr-renamer            # equivalent, via the installed console script

# Build the Windows executable locally (mirrors .github/workflows/build_release.yml)
uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" main.py
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
Config sections: `[Watcher]` (folder, prefix, delay_riavvio), `[OCR]` (box1..box5 crop coordinates
for 2-5 boxes, show_rects debug flag, lang, dpi), `[Filename]` (max_length, remove_leading_zeros). `watcher.run()`
reads and type-converts every value out of the raw `ConfigParser` into plain dicts (`ocr_cfg`,
`name_cfg`) before using them — if you add a config key, update both `config.py`'s prompts and this
parsing step. `prefix` is read with `cfg['Watcher'].get('prefix', 'DOC')` rather than a plain
subscript, since it was added after the original hardcoded `DOC` filter and older `config.ini` files
won't have the key — keep that fallback pattern for any new key added to an existing section.
`box1`..`box5`/`show_rects` follow the same optional-key pattern deliberately: `config.py` never
prompts for them (no generic crop coordinates make sense across documents), so `ocr_cfg['boxes']`
is an empty list until the mouse calibrator (see below) fills it in and writes it back with
`_save_boxes_to_config`; `show_rects` has no setup prompt at all and only takes effect if a user hand-edits
`config.ini` to add it. The box count is configurable from 2 to 5 (`MIN_BOXES`/`MAX_BOXES` in
`watcher.py`) via `+`/`−` buttons in the calibrator itself, not a config prompt; existing
`config.ini` files with only `box1`/`box2` load transparently as a 2-box config.

**Processing pipeline** (`_rinomina_pdf`): `pdf2image.convert_from_path` renders page 1 → `PIL` crops
each of the 2-5 configured boxes → `_preprocess_for_ocr` (grayscale, autocontrast, fixed threshold)
improves each crop → `pytesseract.image_to_string` OCRs each preprocessed crop → `_pulisci_nome`
strips non-word characters, truncates to `max_length`, optionally strips leading zeros → the
non-empty cleaned strings are joined with a single space into the new filename, with `(1)`, `(2)`,
... appended on collision. Before OCR, `_rinomina_pdf` calibrates the crop boxes via `_calibra_box`
whenever fewer than `MIN_BOXES` are configured (first PDF ever processed — the calibrator is
mandatory then, and cancelling skips that file rather than cropping garbage) or whenever
`show_rects` is `True` in `config.ini` (opt-in recalibration). `_calibra_box` opens a Tk window with
the rendered page on a scrollable/zoomable `Canvas` (mouse wheel or +/− buttons, scaled around a
`base_scale` fit-to-screen and clamped by `MAX_ZOOM`/`MAX_DIM`) with colored, numbered selector
buttons (one per box, colors match the drawn rectangles) picking which box the next drag updates,
plus `+ Box`/`− Box` buttons (disabled at 5/2 respectively) to change the box count; saving persists
the new box list to `config.ini` via `_save_boxes_to_config` and applies it immediately to `ocr_cfg`
for the file being processed.

**Watching**: `CMRHandler` (a `watchdog` `FileSystemEventHandler`) reacts to created/moved/modified
events, filters to `*.pdf` files starting with the configured `prefix`, waits for the file to stop
growing (`_file_pronto`, since files typically arrive from a scanner/copier still being written), then
calls `_rinomina_pdf`. A `processati` dict debounces repeat events per path using `delay_riavvio` from
config. `run()` also sweeps the watched folder for pre-existing matching files once at startup, before
starting the observer loop.

**Background mode UI**: when frozen and running in background mode, a system tray icon
(`pystray`, guarded the same way as the `tkinter` import — missing `pystray` just means no tray,
not a crash) offers "Apri log", "Apri cartella monitorata", "Ricalibra box" (opens a file picker
and runs the same `_calibra_box` calibrator against a chosen PDF), and "Esci". This is the only way
to exit a frozen+windowed instance, since it has no console/Ctrl+C available. Log output in
background mode goes through `_RotatingWriter`, which caps `cmr-renamer.log` at ~1MB with one
backup (`cmr-renamer.log.1`) instead of growing unbounded.

**Language note**: user-facing console strings and internal helper names (`_rinomina_pdf`,
`_pulisci_nome`, `_file_pronto`) are Italian (the tool's target users); docstrings/comments are
English. Match the existing convention for the kind of string/identifier you're touching.
