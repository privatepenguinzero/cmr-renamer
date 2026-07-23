# Design: Tray icon, log rotation, OCR preprocessing, multi-box OCR

Date: 2026-07-23

## Purpose

Four related quality-of-life and accuracy improvements to CMR Renamer, identified from a review
of the current codebase (`cmr_renamer/watcher.py`, `cmr_renamer/config.py`):

1. The frozen `--windowed` executable has zero visible UI once configured — no way to tell it's
   running, check status, or trigger recalibration without hand-editing `config.ini` and restarting.
2. The background-mode log file (`cmr-renamer.log`) is opened in append mode forever with no size
   cap, so it grows unbounded on a long-running install.
3. OCR crops go straight from `pdf2image` into `pytesseract` with no preprocessing, leaving accuracy
   on the table for scanner output.
4. The OCR pipeline is hardcoded to exactly two boxes (document number + company name), but some
   document layouts need more fields extracted into the filename.

All four are scoped as one design since they're all small, additive changes to the same two files,
none require decomposition into separate milestones.

## 1. Icon assets

- New `assets/icon.ico` — a simple generated placeholder glyph (flat-color document/scan icon),
  multi-resolution (16/32/48/256px) so it renders correctly in the taskbar, Alt-Tab, and File
  Explorer. Placeholder now; swappable later without any code change.
- The same source image is reused for the tray icon — `pystray` accepts a `PIL.Image` directly, so
  one asset file serves both the `.exe` icon and the tray icon. No duplicate icon logic.
- The PyInstaller build command gains `--icon=assets/icon.ico`. Both the local build command
  documented in `CLAUDE.md` and `.github/workflows/build_release.yml` are updated to match.

## 2. System tray integration

- New dependency: `pystray`, added to `pyproject.toml`.
- Import-guarded the same way `tkinter`/`ImageTk` already are in `watcher.py` (`TKINTER_AVAILABLE`
  pattern): if `pystray` fails to import, the app silently falls back to today's plain sleep-loop
  with no tray icon, rather than crashing.
- **Scope**: tray is only active when `_is_frozen()` is true and the app reaches background mode
  (both the "frozen, config just created" and "frozen, config already exists" paths in `run()`).
  Running from source is completely unchanged — no tray, same console behavior as today.
- **Threading model**: `Observer` keeps running in its own thread exactly as today. The
  `pystray.Icon` also runs in a background thread via `icon.run()` (safe on Windows; only macOS
  strictly requires the main thread). The main thread, instead of `while True: time.sleep(1)`,
  waits on a `threading.Event`. The tray's **Exit** action sets that event and calls
  `observer.stop()` / `icon.stop()` for clean shutdown. This is the only way to exit a
  frozen+windowed instance, since it has no console/Ctrl+C available anyway.
- **Menu items**:
  - **Open log file** — `os.startfile(log_path)`.
  - **Open watched folder** — `os.startfile(cartella)`.
  - **Recalibrate boxes** — opens a file picker (tkinter `askopenfilename`, PDF filter), renders
    page 1 of the chosen PDF, and immediately opens the existing calibrator against it, saving via
    the existing `_save_boxes_to_config`. Reuses current calibration code as-is.
  - **Exit** — always present; stops observer + tray cleanly.

## 3. Log rotation

- New small helper class, e.g. `_RotatingWriter`, replacing the plain `open(log_path, 'a', ...)`
  file object currently assigned in `_setup_file_logging`.
- On each `write()`, checks the underlying file size; once a cap (1MB) is hit, rotates
  `cmr-renamer.log` → `cmr-renamer.log.1` (overwriting any previous `.1`) and truncates a fresh
  `cmr-renamer.log`. Keeps exactly one backup — no unbounded growth, no external log library.
- Deliberately *not* migrating to Python's `logging` module — the codebase uses `print()` with
  Italian/emoji strings at every call site, and rewriting all of them would be an unrelated, large
  refactor. `_RotatingWriter` is a drop-in replacement for the file object already assigned to
  `sys.stdout`/`sys.stderr` in `_setup_file_logging`, so no call sites change.

## 4. OCR image preprocessing

- New helper `_preprocess_for_ocr(img: Image) -> Image`, applied to each box crop immediately
  before `pytesseract.image_to_string`.
- PIL-only pipeline (no new dependency): grayscale (`convert('L')`) → `ImageOps.autocontrast` →
  fixed-threshold binarization (`point(lambda p: 255 if p > 128 else 0)`). The threshold of 128 is a
  starting default tuned during manual verification (see Testing), not derived per-image.
- Always on, no config toggle — matches the project's existing lean, YAGNI config style.

## 5. Configurable OCR box count (2-5)

- **Internal representation**: the calibrator's box state generalizes from a `box1`/`box2` dict to
  an ordered list of coordinate tuples (2-5 entries). Colors cycle through a fixed palette
  (`red, blue, green, orange, purple`). Labels stay `"Box 1 (numero documento)"` /
  `"Box 2 (ragione sociale)"` for the first two entries (matching today's semantics), and generic
  `"Box 3"` / `"Box 4"` / `"Box 5"` for any additional ones.
- **Calibrator UI**: adds a `"+ Box"` button (disabled at 5 boxes) and a `"− Box"` button (disabled
  at 2 boxes) alongside the existing per-box selector buttons.
  - `+ Box` appends a new small default rectangle and makes it the active box for immediate dragging.
  - `− Box` removes the *currently active* box (not just the last one), then activates the previous
    box in the list.
  - `_calibra_box`'s signature changes from `(img, box1, box2)` to `(img, boxes: list[tuple])`,
    returning the saved list (2-5 tuples) or `None` on cancel.
- **Config storage**: keeps the existing `box1`, `box2`, ... `boxN` key naming in `config.ini` —
  however many are present (2-5). This is what makes existing 2-box configs load transparently as a
  2-box setup with zero migration step. `_save_boxes_to_config` generalizes to accept a list and
  write exactly that many keys, deleting any higher-numbered leftover keys if the box count shrank
  since the last save (e.g. going from 4 boxes back down to 3 removes the stale `box4` key).
- **Processing pipeline**: `_rinomina_pdf` loops over `ocr_cfg['boxes']` (OCR + clean each crop via
  `_preprocess_for_ocr` and `_pulisci_nome`), then joins only the *non-empty* cleaned strings with a
  single space. This also fixes a latent double-space bug the current 2-box
  `f"{clean1} {clean2}"` pattern would hit once extended to N boxes (an empty middle box would
  otherwise leave a double space in the joined result).
- **Backward compatibility**: an existing `config.ini` with only `box1`/`box2` loads transparently
  as a 2-box config; behavior for existing installs is unchanged unless they open the calibrator and
  add more boxes.

## Error handling

- All four features fail soft:
  - Missing `pystray` → no tray, same as missing `tkinter` today.
  - Log rotation failure (e.g. permission error renaming the backup) → falls back to appending to
    the existing file rather than crashing the writer.
  - Preprocessing runs before OCR, not gated by any try/except beyond the existing outer
    `try/except` in `_rinomina_pdf`, which already catches and logs any exception per-file without
    stopping the watcher.
  - Calibrator add/remove is purely in-memory UI state until "Salva" is pressed — cancelling
    discards changes exactly as today.

## Testing

No automated test suite exists in this repo (per `CLAUDE.md`). Verification is manual:

- Run from source (`python main.py`) and confirm behavior is unchanged (no tray, plain console).
- Build the frozen exe locally and confirm: taskbar/tray icon renders, all four tray menu items work,
  Exit cleanly stops the process.
- Confirm log file rotates once it crosses the size cap (can lower the cap temporarily during
  manual testing).
- Process a real PDF and eyeball whether preprocessing changes OCR output quality (no automated OCR
  accuracy metric — subjective before/after check).
- In the calibrator: add up to 5 boxes, remove down to 2, confirm buttons disable at the limits,
  confirm saved `config.ini` reflects the right number of `boxN` keys, confirm an old-style
  `box1`/`box2`-only `config.ini` still loads and processes correctly.

## Out of scope

- Configurable preprocessing (on/off toggle) — always-on per decision above.
- Tray icon when running from source — frozen background mode only.
- Any change to OCR language/DPI/prefix config, which are unaffected by this design.
