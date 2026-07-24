# Design: GUI initial configuration wizard

Date: 2026-07-24

## Purpose

`config.py`'s `load_or_create_config` currently creates `config.ini` on first run via plain console
`input()` prompts (`_prompt_console`), except the watched folder, which already uses a native
Tkinter folder picker (`_prompt_for_folder`) when available. For the frozen `--windowed` exe with
no `config.ini` yet, this means briefly allocating a Windows console just to show those text
prompts (`_alloc_console`/`_free_console` in `watcher.run()`), then freeing it once setup
completes. The user wants the whole initial setup to be one Tkinter window instead — consistent
with the box calibrator window already used elsewhere in the app — removing the console flash
entirely when `tkinter` is available.

## Scope

- Replaces every prompt in `load_or_create_config` (folder, prefix, delay_riavvio, lang, dpi,
  max_length, remove_leading_zeros) with one Tkinter form window.
- GUI is preferred whenever `tkinter` is available (same `TKINTER_AVAILABLE` fallback pattern
  `config.py` already uses for the folder picker) — applies whether frozen or running from source,
  matching how the existing folder picker already behaves regardless of frozen state.
- If `tkinter` is unavailable, or the user closes the window without saving, falls back to the
  existing console-prompt flow unchanged — no existing code is removed, the GUI path is added
  alongside it.
- `watcher.run()`'s frozen-with-no-config branch skips `_alloc_console()`/`_free_console()`
  entirely when `TKINTER_AVAILABLE` is true (no console needed); the console allocation remains as
  the fallback for the rare case of a frozen build without `tkinter`.
- Out of scope: any change to `config.ini`'s format/keys, the box calibrator, or the tray icon.

## 1. Form layout and fields

A single Tkinter window, top to bottom:
- **Cartella**: text entry pre-fillable by typing, plus a "Sfoglia..." button that opens the
  existing `askdirectory` picker and populates the entry.
- **Prefisso**: text entry, default `DOC`.
- **Delay tra rilevamenti (secondi)**: text entry, default `3`.
- **Lingua OCR**: three checkboxes (`eng`, `ita`, `deu`) — `eng` checked by default — plus a text
  entry labeled "altre lingue (es. fra, separate da +)" for anything not bundled (system-installed
  Tesseract languages, when running from source). On save, the checked codes and the extra text are
  joined with `+` into the same `lang` string format already used today (e.g. `eng+ita`).
- **DPI per conversione PDF**: text entry, default `300`.
- **Lunghezza massima per parte del nome file**: text entry, default `60`.
- **Rimuovere zeri iniziali dai numeri?**: checkbox, checked by default.
- **Conferma** button (no "Annulla" — initial setup is mandatory, there's nothing sensible to
  cancel back to). Closing the window via the OS close button is treated the same as "GUI
  unavailable": falls back to the console flow.

## 2. Validation

On clicking "Conferma":
- Delay, DPI, and max length must each parse as a positive integer.
- At least one language must be selected or typed (checkboxes + extra-text field, combined, must
  not resolve to an empty string).
- Folder and prefix have no format validation (matches today's behavior — an empty or
  not-yet-existing folder is valid; `watcher.run()` already handles a not-yet-existing watched
  folder gracefully by waiting for it to be created).

If validation fails, an inline message in the window explains what to fix; the window stays open
and nothing is written until it passes.

## 3. Implementation shape

- New `_prompt_with_gui(defaults: dict) -> dict | None` in `config.py`, returning a dict with the
  same keys `load_or_create_config` already assembles by hand today, or `None` on fallback
  (`tkinter` unavailable or window closed without saving).
- `load_or_create_config` tries `_prompt_with_gui` first when `TKINTER_AVAILABLE`; if it returns
  `None`, falls through to today's console-prompt code unchanged.
- `watcher.run()`: the frozen-with-no-config branch checks `TKINTER_AVAILABLE` (the flag already
  defined in `watcher.py` for the calibrator) before calling `_alloc_console()`; if true, skips
  straight to `load_or_create_config()` with no console, then proceeds to file logging as today.

## Error handling

- `tkinter` unavailable at import time → falls back to console flow, identical to today.
- Window closed without saving → same fallback as above (not a crash, not a silent no-op — setup
  still completes via console).
- Invalid numeric input or no language selected → inline message, save blocked until fixed.
- Folder picker cancelled inside the form → leaves the text entry as-is (typed manually or empty),
  same tolerant behavior as `_prompt_for_folder` today.

## Testing

No automated test suite exists in this repo (per `CLAUDE.md`). Validation logic (integer parsing,
"at least one language" check, `+`-joining selected languages) is pure and testable without
`tkinter`. The window itself — layout, the browse button, closing without saving falling back to
console — requires manual verification on a machine with `tkinter`, same limitation as the box
calibrator's own GUI pieces.

## Out of scope

- Any change to `config.ini`'s stored format (still plain strings per key, just written from a
  different capture path).
- Editing an *existing* configuration through this window (this replaces only the missing-config
  first-run path — an already-configured install has no code path that revisits this).
- Any change to the box calibrator or tray icon.
