# Design: Multi-file preview in the box calibrator

Date: 2026-07-24

## Purpose

Today, calibrating OCR boxes (`_calibra_box` in `cmr_renamer/watcher.py`) always shows a single
PDF page: either the one file that triggered mandatory first-run calibration, or — via the tray's
"Ricalibra box" — a single file picked through a file-open dialog. There is no way to check whether
a box position that looks right on one document also lands correctly on other documents in the
same watched folder, short of saving, waiting for the next real file, and eyeballing the renamed
output.

This design adds a scrollable sidebar listing the PDFs in the watched folder, so the user can
switch the calibrator's preview between documents live while calibrating, and visually confirm the
box positions hold across multiple real samples before saving.

## Scope

- Both calibration entry points gain the sidebar: the mandatory first-run calibration inside
  `_rinomina_pdf`, and the tray's "Ricalibra box" (`_recalibra` in `_build_tray_icon`).
- The sidebar lists **every** `*.pdf` in the watched folder, not just files matching the configured
  prefix — useful for checking box alignment even against documents the watcher itself would never
  pick up.
- Switching files re-renders that file's page 1 and redraws the existing boxes at their current
  coordinates. No OCR runs during a switch — this is a visual check only, not a live OCR preview.
  Boxes stay shared/global: dragging or adding/removing a box applies regardless of which file is
  currently displayed, exactly as box state works today.
- The tray's file-open dialog for picking a single PDF is removed; "Ricalibra box" now scans the
  watched folder directly, the same way the mandatory calibration path already does.

## 1. `_calibra_box` signature and rendering

- Signature changes from `_calibra_box(img, boxes)` to
  `_calibra_box(pdf_paths: list[str], initial_path: str, boxes: list, dpi: int)`.
- Internally keeps a `path → PIL.Image` cache (page 1, rendered via `convert_from_path` at the
  configured `dpi`). `initial_path` is rendered eagerly (needed to size the window); every other
  file renders lazily, on first selection, then stays cached for the rest of the session.
- Box coordinates are never transformed on file switch — they stay in the same page-pixel space
  (consistent because `dpi` is one global config value applied to every render). This is
  deliberate: if a document has a different page size or a shifted layout, the boxes visibly
  fail to line up, which is exactly the mismatch this feature exists to surface.
- `base_scale` (fit-to-screen sizing) is computed once from `initial_path`'s dimensions and reused
  for every subsequent file. A later file with different page dimensions just extends the
  scrollable canvas area — it does not resize the window or change the zoom level out from under
  the user.

## 2. Sidebar UI

- New `Listbox` + `Scrollbar` (matching the existing `tkinter` widgets already imported), placed to
  the left of the existing canvas, listing PDF filenames sorted alphabetically.
- The file that triggered calibration (`initial_path`) is selected/highlighted on open. For the
  tray entry point, where there is no triggering file, the first file alphabetically is selected.
- Selecting a row loads that file's cached/rendered image, swaps it onto the canvas, and redraws
  all boxes — reusing the existing `render()`/`draw_all_boxes()` functions, which become
  image-source-aware instead of operating on a single fixed `img`.
- No search/filter field, no thumbnails/eager pre-rendering — a plain sorted list is enough given
  this tool's already-minimal UI style, and lazy rendering keeps opening the calibrator fast
  regardless of folder size.
- All existing controls (box selector buttons, `+ Box`/`− Box`, zoom controls, `Salva`/`Annulla`)
  are unchanged in behavior.

## 3. Entry-point wiring

- **Mandatory first-run calibration** (`_rinomina_pdf`): the watched folder is already available as
  `os.path.dirname(pdf_path)` — no new parameter threading required. Scans that folder for `*.pdf`,
  passes the list plus `initial_path=pdf_path` (the file currently being processed).
- **Tray "Ricalibra box"** (`_recalibra`): the `askopenfilename` file dialog is removed. `cartella`
  is already in the closure (passed into `_build_tray_icon`); scans it for `*.pdf` and passes the
  list with `initial_path` = first file alphabetically.

## Error handling

- **Empty folder** (tray entry point only — the mandatory path always has at least the triggering
  file): if the watched folder contains no PDFs when "Ricalibra box" is invoked, print
  `⚠️ Nessun PDF trovato nella cartella monitorata.` and return without opening the calibrator.
- **File becomes unreadable mid-session** (e.g. the watcher renames another file out from under the
  list while the calibrator is open): wrap the lazy per-file render in a `try/except`; on failure,
  leave the previously-displayed image on the canvas and print a warning instead of crashing the
  calibrator window.
- All other error handling (calibration lock, cancel discarding changes, outer `try/except` in
  `_rinomina_pdf`) is unchanged.

## Testing

No automated test suite exists in this repo (per `CLAUDE.md`). Verification is manual, from source:

- Point the watched folder at a directory with 3-4 sample PDFs, delete `config.ini`'s `box*` keys
  to force mandatory calibration, and confirm the sidebar lists all of them with the triggering
  file pre-selected, and that switching files keeps box positions fixed while changing the
  background image.
- With boxes already configured, use the tray's "Ricalibra box" and confirm it opens directly
  against the watched folder's file list (no file-open dialog), with the first file alphabetically
  selected.
- Empty the watched folder and confirm "Ricalibra box" prints the warning and does not open a
  window.
- Confirm dragging/adding/removing boxes while a non-initial file is displayed, then saving,
  persists correctly to `config.ini` (same as today).

## Out of scope

- Live OCR preview per file (explicitly rejected — visual box-position check only, no OCR during
  switch).
- Eager pre-rendering/thumbnails of all files up front.
- Search/filter box for large folders.
- Any change to how boxes are stored in `config.ini` (still `box1`..`box5` keys, unaffected by this
  design).
