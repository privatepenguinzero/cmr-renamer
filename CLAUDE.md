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
uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" --add-data "vendor/poppler;vendor/poppler" --add-data "vendor/tesseract;vendor/tesseract" main.py
```

There is no test suite, linter, or formatter configured in this repo — don't assume `pytest`/`ruff`
exist. Poppler and Tesseract are vendored under `vendor/` (via Git LFS) and bundled into the built
exe — see "Bundled native dependencies" below. Running from source still needs both installed
system-wide and on `PATH`, exactly as before this bundling was added; the CI workflow no longer
installs Tesseract via choco since the build now uses the vendored copy instead.

**Git LFS is required to clone/build this repo**: run `git lfs install` once per machine before
cloning, otherwise `vendor/` will contain empty LFS pointer files instead of the real binaries.

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
- Frozen + no `config.ini`, `tkinter` unavailable: allocates a Windows console
  (`_alloc_console`/`_free_console`, via `ctypes.windll.kernel32`) so the interactive console
  setup prompts are visible, then frees it and switches to file logging (`_setup_file_logging`
  redirects `sys.stdout`/`stderr` to `cmr-renamer.log`) for normal background operation.
- Frozen + no `config.ini`, `tkinter` available: no console at all — `_setup_file_logging` runs
  first (a `--windowed` build with no console has `sys.stdout`/`stderr` as `None`, so anything
  printed before logging is set up would crash), then `load_or_create_config` shows the GUI setup
  form directly.
- Frozen + config exists: goes straight to background/file-logging mode — no console at all, since
  the exe is built with `--windowed`.

**Config lives next to the executable (or CWD when running from source)**, in `config.ini`, and is
created interactively on first run by `config.py` (`load_or_create_config`). It prefers a single
Tkinter form (`_prompt_with_gui`) covering every setting — folder (with a "Sfoglia..." button
reusing the same `askdirectory` picker), prefix, delay, OCR language (checkboxes for the bundled
`eng`/`ita`/`deu` plus a free-text field for extra codes, joined with `+` via `_build_lang_string`),
dpi, max filename length, and the leading-zeros checkbox — validating delay/dpi/max_length as
positive integers (`_parse_positive_int`) and requiring at least one language before saving.
Falls back to the original per-field console prompts if `tkinter` is unavailable or the form is
closed without saving.
Config sections: `[Watcher]` (folder, prefix, delay_riavvio), `[OCR]` (box1..box5 crop coordinates
for 2-5 boxes, anchor_x/anchor_y content-anchor reference, show_rects debug flag, lang, dpi),
`[Filename]` (max_length, remove_leading_zeros). `watcher.run()`
reads and type-converts every value out of the raw `ConfigParser` into plain dicts (`ocr_cfg`,
`name_cfg`) before using them — if you add a config key, update both `config.py`'s prompts and this
parsing step. `prefix` is read with `cfg['Watcher'].get('prefix', 'DOC')` rather than a plain
subscript, since it was added after the original hardcoded `DOC` filter and older `config.ini` files
won't have the key — keep that fallback pattern for any new key added to an existing section.
`box1`..`box5`/`anchor_x`/`anchor_y`/`show_rects` follow the same optional-key pattern deliberately:
`config.py` never prompts for them (no generic crop coordinates or content anchor make sense across
documents), so `ocr_cfg['boxes']` is an empty list and `ocr_cfg['anchor']` is `None` until the mouse
calibrator (see below) fills them in and writes them back with `_save_calibration_to_config`;
`show_rects` has no setup prompt at all and only takes effect if a user hand-edits `config.ini` to
add it. The box count is configurable from 2 to 5 (`MIN_BOXES`/`MAX_BOXES` in
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
`show_rects` is `True` in `config.ini` (opt-in recalibration). `_calibra_box(pdf_paths, initial_path,
boxes, dpi)` opens a Tk window with the rendered page on a scrollable/zoomable `Canvas` (mouse wheel
or +/− buttons, scaled around a `base_scale` fit-to-screen and clamped by `MAX_ZOOM`/`MAX_DIM`),
plus a sidebar `Listbox` of every PDF in the watched folder (`_list_watched_pdfs`, sorted
alphabetically, `initial_path` preselected) so box placement can be checked live against multiple
real documents before saving — each page renders lazily via `_render_pdf_page` on first selection
and is cached for the rest of the session. The stored box coordinates themselves stay fixed to
`initial_path` as the session's reference frame, but what's *drawn* on a non-reference file is
shifted by `_compute_anchor_shift` (the same drift-correction math `_resolve_crop_boxes` uses
during real processing) so the sidebar preview shows where the boxes would actually land after
correction — dragging on a non-reference file un-shifts the dropped position before storing it, so
the saved coordinate stays correct regardless of which file was on screen while dragging.
Colored, numbered selector buttons (one per box, colors match the drawn rectangles) pick which box
the next drag updates, plus `+ Box`/`− Box` buttons (disabled at 5/2 respectively) to change the box
count; saving computes a *content anchor* via `_detect_content_anchor` (where the page's content
stops being white, from the top and from the left — a fast Pillow-only row/column darkness scan,
no numpy/OpenCV) on whichever page is on screen at that moment, and persists both the box list and
that anchor to `config.ini` via `_save_calibration_to_config`, applying them immediately to
`ocr_cfg` for the file being processed. From then on, every file `_rinomina_pdf` processes runs
`_resolve_crop_boxes`, which re-detects the anchor on that page and shifts all boxes by the
difference from the saved reference before cropping — compensating for a scanner/copier feeding the
page a few millimeters off from where it was during calibration. If the anchor can't be detected
(near-blank page) or the shift looks implausible (beyond `MAX_ANCHOR_SHIFT_MM`), it falls back to
the uncorrected calibrated boxes and logs a warning rather than blocking the file. `config.ini`
without `anchor_x`/`anchor_y` (calibrated before this existed) simply skips correction — same
optional-key pattern as `box1..5`/`show_rects`.

**Watching**: `CMRHandler` (a `watchdog` `FileSystemEventHandler`) reacts to created/moved/modified
events, filters to `*.pdf` files starting with the configured `prefix`, waits for the file to stop
growing (`_file_pronto`, since files typically arrive from a scanner/copier still being written), then
calls `_rinomina_pdf`. A `processati` dict debounces repeat events per path using `delay_riavvio` from
config. `run()` also sweeps the watched folder for pre-existing matching files once at startup, before
starting the observer loop.

**Background mode UI**: when frozen and running in background mode, a system tray icon
(`pystray`, guarded the same way as the `tkinter` import — missing `pystray` just means no tray,
not a crash) offers "Apri log", "Apri cartella monitorata", "Ricalibra box" (scans the watched
folder for PDFs via `_list_watched_pdfs`, the same way mandatory first-run calibration does, and
opens the same `_calibra_box` calibrator against that list — no file picker; an empty folder just
prints a warning and does nothing), and "Esci". This is the only way
to exit a frozen+windowed instance, since it has no console/Ctrl+C available. Log output in
background mode goes through `_RotatingWriter`, which caps `cmr-renamer.log` at ~1MB with one
backup (`cmr-renamer.log.1`) instead of growing unbounded.

**Language note**: user-facing console strings and internal helper names (`_rinomina_pdf`,
`_pulisci_nome`, `_file_pronto`) are Italian (the tool's target users); docstrings/comments are
English. Match the existing convention for the kind of string/identifier you're touching.
