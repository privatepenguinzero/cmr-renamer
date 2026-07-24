# Calibrator Drift Preview + GUI Setup Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show drift-corrected box positions when browsing files in the calibrator's sidebar, replace the console-based initial setup with a Tkinter GUI form, and fix a tray-icon bug where "Apri cartella monitorata" fails on UNC network paths.

**Architecture:** The calibrator (`_calibra_box`) gains a session-fixed reference anchor and a per-file preview shift, computed via a shift-calculation helper extracted from the already-shipped `_resolve_crop_boxes` (shared, not duplicated). `config.py` gains a Tkinter form (`_prompt_with_gui`) that `load_or_create_config` tries before falling back to its existing console prompts, and `watcher.run()` skips allocating a console for setup when `tkinter` is available. The tray bug fix normalizes paths with `os.path.normpath()` before `os.startfile()`.

**Tech Stack:** Python, `tkinter` (existing dependency pattern, no new library), `configparser`.

## Global Constraints

- Specs: `docs/superpowers/specs/2026-07-24-calibrator-drift-preview-design.md` and
  `docs/superpowers/specs/2026-07-24-gui-initial-setup-design.md`.
- No automated test suite exists in this repo (per `CLAUDE.md`) — verification below uses ad-hoc
  `python3` scripts, not `pytest`.
- This sandbox has no `tkinter` installed — GUI-only behavior (the calibrator window itself, the
  new setup form) cannot be exercised end-to-end here. Every task says explicitly what is and
  isn't verified in this environment; do not claim GUI behavior was verified when it wasn't.
- No new dependencies. `tkinter` usage follows the existing `TKINTER_AVAILABLE` try/except-import
  fallback pattern already used in both `watcher.py` and `config.py`.
- The calibrator's preview correction is silent (no visual indicator on fallback) — matches
  `_resolve_crop_boxes`'s existing fail-soft behavior, per the approved spec.
- The GUI setup form validates delay/dpi/max_length as positive integers and requires at least one
  OCR language, blocking save with an inline message until fixed; it must not remove or alter the
  existing console-prompt fallback code path.
- `run()`'s console-allocation change must not introduce a crash: a frozen `--windowed` build has
  `sys.stdout`/`sys.stderr` as `None` until either a console is allocated or
  `_setup_file_logging` runs — any `print()` before one of those two happens will raise
  `AttributeError`. When skipping console allocation, `_setup_file_logging` must run *before*
  `load_or_create_config` is called.

---

### Task 1: Fix tray "Apri cartella"/"Apri log" on UNC paths

**Files:**
- Modify: `cmr_renamer/watcher.py` (`_open_log`, `_open_folder` inside `_build_tray_icon`)

**Interfaces:** none new — internal fix only.

Root cause (confirmed via `ntpath.normpath`, see below): `config.ini`'s `folder` value can contain
forward slashes (Tkinter's `askdirectory` returns paths with `/` on Windows, even for UNC network
shares), and `os.startfile`/`ShellExecute` doesn't reliably resolve a UNC path in that form —
`os.startfile('//server/share/x')` raises `WinError 2` even though the share exists.
`os.path.normpath` (which is `ntpath.normpath` on Windows) fixes this.

- [ ] **Step 1: Confirm the root cause independent of the app (uses `ntpath`, works on any OS)**

```bash
python3 -c "
import ntpath
p = '//W2012srv-fs/Archivio/Vendite condivisi/CMR'
result = ntpath.normpath(p)
print(repr(result))
assert result == r'\\\\W2012srv-fs\Archivio\Vendite condivisi\CMR', result
print('OK: normpath produces a well-formed UNC path')
"
```

Expected output: `OK: normpath produces a well-formed UNC path`.

- [ ] **Step 2: Apply the fix**

In `cmr_renamer/watcher.py`, replace:

```python
    def _open_log(icon, item):
        try:
            os.startfile(log_path)
        except Exception as e:
            print(f"⚠️ Impossibile aprire il log: {e}")

    def _open_folder(icon, item):
        try:
            os.startfile(cartella)
        except Exception as e:
            print(f"⚠️ Impossibile aprire la cartella: {e}")
```

with:

```python
    def _open_log(icon, item):
        try:
            os.startfile(os.path.normpath(log_path))
        except Exception as e:
            print(f"⚠️ Impossibile aprire il log: {e}")

    def _open_folder(icon, item):
        try:
            os.startfile(os.path.normpath(cartella))
        except Exception as e:
            print(f"⚠️ Impossibile aprire la cartella: {e}")
```

- [ ] **Step 3: Verify the wiring (mocks `os.startfile`, which doesn't exist on this Linux
      sandbox, so this proves the code *calls* `os.path.normpath` correctly — the Windows-specific
      transform itself was already proven in Step 1)**

```bash
python3 - <<'EOF'
import sys, os, threading
sys.path.insert(0, '.')
from cmr_renamer import watcher
from PIL import Image

if not watcher.PYSTRAY_AVAILABLE:
    print("SKIP: pystray not installed in this environment")
    sys.exit(0)

calls = []
os.startfile = lambda path: calls.append(path)  # os.startfile doesn't exist on Linux; add it for this test

icon_img = Image.new('RGB', (16, 16), 'white')
ocr_cfg = {'boxes': [], 'dpi': 150, 'anchor': None}
cartella = '//W2012srv-fs/Archivio/Vendite condivisi/CMR'
log_path = '/tmp/log with spaces.txt'
tray = watcher._build_tray_icon(icon_img, ocr_cfg, log_path, cartella, threading.Event())

folder_item = next(i for i in tray.menu.items if str(i.text) == 'Apri cartella monitorata')
folder_item(tray)
assert calls == [os.path.normpath(cartella)], calls
print('OK folder path normalized:', calls[-1])

log_item = next(i for i in tray.menu.items if str(i.text) == 'Apri log')
log_item(tray)
assert calls[-1] == os.path.normpath(log_path), calls
print('OK log path normalized:', calls[-1])
EOF
```

Expected output: `OK folder path normalized: ...` then `OK log path normalized: ...` (or `SKIP: ...`
if `pystray` isn't available — treat as not verified, not a pass).

- [ ] **Step 4: Full-file syntax check**

```bash
python3 -m py_compile cmr_renamer/watcher.py
```

Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Fix tray Apri cartella/Apri log failing on UNC paths with forward slashes"
```

---

### Task 2: Extract `_compute_anchor_shift`, refactor `_resolve_crop_boxes`

**Files:**
- Modify: `cmr_renamer/watcher.py` (insert new function before `_resolve_crop_boxes`; refactor its
  body)

**Interfaces:**
- Produces: `_compute_anchor_shift(current: tuple[int,int] | None, reference: tuple[int,int] | None, dpi: int) -> tuple[int, int]`
  — `(0, 0)` if either anchor is `None` or the shift exceeds `MAX_ANCHOR_SHIFT_MM`, else the real
  `(dx, dy)`. Used by both `_resolve_crop_boxes` (this task) and the calibrator (Task 3).
- `_resolve_crop_boxes`'s own signature and behavior are unchanged — this is a pure internal
  refactor, verified against the exact same scenarios its original implementation was verified
  against, to catch any regression.

**Correctness note for the implementer:** `_resolve_crop_boxes` must still print its "shift beyond
plausibility limit" warning only when the shift was *actually clamped* — not whenever the final
shift happens to be `(0, 0)` (a real, un-clamped zero shift, i.e. current anchor exactly equals the
reference, is valid and must NOT print a warning). Compare the shared helper's output against the
raw, unclamped `(dx, dy)` to tell the two cases apart — do not just check `(dx, dy) == (0, 0)`.

- [ ] **Step 1: Add `_compute_anchor_shift`**

Insert immediately before `def _resolve_crop_boxes(...)`:

```python
def _compute_anchor_shift(current: "tuple[int, int] | None", reference: "tuple[int, int] | None", dpi: int) -> "tuple[int, int]":
    """Calcola lo spostamento (dx, dy) tra due ancore di contenuto.

    Ritorna (0, 0) se manca un'ancora, o se lo spostamento supera la soglia plausibile
    (MAX_ANCHOR_SHIFT_MM convertita in pixel in base al dpi).
    """
    if reference is None or current is None:
        return (0, 0)
    dx = current[0] - reference[0]
    dy = current[1] - reference[1]
    max_shift_px = dpi * MAX_ANCHOR_SHIFT_MM / 25.4
    if abs(dx) > max_shift_px or abs(dy) > max_shift_px:
        return (0, 0)
    return (dx, dy)
```

- [ ] **Step 2: Refactor `_resolve_crop_boxes` to use it**

Replace:

```python
def _resolve_crop_boxes(img: "Image.Image", ocr_cfg: dict) -> list:
    """Ritorna i box da ritagliare, corretti per la deriva di scansione quando possibile.

    Se non c'è un'ancora di riferimento salvata, se l'ancora non è rilevabile sulla pagina
    corrente, o se lo spostamento rilevato supera la soglia plausibile, ritorna i box calibrati
    senza modifiche — il file viene comunque elaborato, solo senza correzione.
    """
    boxes = ocr_cfg['boxes']
    reference = ocr_cfg.get('anchor')
    if reference is None:
        return boxes

    current = _detect_content_anchor(img)
    if current is None:
        print("⚠️ Ancora di contenuto non rilevabile: uso i box calibrati senza correzione deriva.")
        return boxes

    dx = current[0] - reference[0]
    dy = current[1] - reference[1]
    max_shift_px = ocr_cfg['dpi'] * MAX_ANCHOR_SHIFT_MM / 25.4
    if abs(dx) > max_shift_px or abs(dy) > max_shift_px:
        print(f"⚠️ Spostamento rilevato ({dx}, {dy}px) oltre la soglia plausibile: uso i box calibrati senza correzione deriva.")
        return boxes

    return [(x1 + dx, y1 + dy, x2 + dx, y2 + dy) for (x1, y1, x2, y2) in boxes]
```

with:

```python
def _resolve_crop_boxes(img: "Image.Image", ocr_cfg: dict) -> list:
    """Ritorna i box da ritagliare, corretti per la deriva di scansione quando possibile.

    Se non c'è un'ancora di riferimento salvata, se l'ancora non è rilevabile sulla pagina
    corrente, o se lo spostamento rilevato supera la soglia plausibile, ritorna i box calibrati
    senza modifiche — il file viene comunque elaborato, solo senza correzione.
    """
    boxes = ocr_cfg['boxes']
    reference = ocr_cfg.get('anchor')
    if reference is None:
        return boxes

    current = _detect_content_anchor(img)
    if current is None:
        print("⚠️ Ancora di contenuto non rilevabile: uso i box calibrati senza correzione deriva.")
        return boxes

    raw_dx = current[0] - reference[0]
    raw_dy = current[1] - reference[1]
    dx, dy = _compute_anchor_shift(current, reference, ocr_cfg['dpi'])
    if (dx, dy) != (raw_dx, raw_dy):
        print(f"⚠️ Spostamento rilevato ({raw_dx}, {raw_dy}px) oltre la soglia plausibile: uso i box calibrati senza correzione deriva.")
        return boxes

    return [(x1 + dx, y1 + dy, x2 + dx, y2 + dy) for (x1, y1, x2, y2) in boxes]
```

- [ ] **Step 3: Regression-verify `_resolve_crop_boxes` (same 4 scenarios as when it first shipped)**

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
from cmr_renamer.watcher import _resolve_crop_boxes, _detect_content_anchor

boxes = [(10, 10, 100, 40)]

ocr_cfg = {'boxes': boxes, 'anchor': None, 'dpi': 150}
img = Image.new('RGB', (400, 500), (255, 255, 255))
assert _resolve_crop_boxes(img, ocr_cfg) == boxes
print('OK no reference anchor')

img2 = Image.new('RGB', (400, 500), (255, 255, 255))
draw = ImageDraw.Draw(img2)
draw.rectangle([20, 30, 380, 35], fill=(0, 0, 0))
draw.rectangle([20, 30, 25, 480], fill=(0, 0, 0))
current = _detect_content_anchor(img2)
assert current is not None
ocr_cfg2 = {'boxes': boxes, 'anchor': (10, 20), 'dpi': 150}
result = _resolve_crop_boxes(img2, ocr_cfg2)
dx = current[0] - 10
dy = current[1] - 20
assert result == [(10 + dx, 10 + dy, 100 + dx, 40 + dy)], (result, current)
print('OK shifted boxes', result)

blank = Image.new('RGB', (400, 500), (255, 255, 255))
ocr_cfg3 = {'boxes': boxes, 'anchor': (10, 20), 'dpi': 150}
assert _resolve_crop_boxes(blank, ocr_cfg3) == boxes
print('OK blank current page fallback')

img4 = Image.new('RGB', (2000, 2500), (255, 255, 255))
draw4 = ImageDraw.Draw(img4)
draw4.rectangle([1000, 1200, 1980, 1210], fill=(0, 0, 0))
draw4.rectangle([1000, 1200, 1010, 2480], fill=(0, 0, 0))
ocr_cfg4 = {'boxes': boxes, 'anchor': (10, 20), 'dpi': 150}
assert _resolve_crop_boxes(img4, ocr_cfg4) == boxes
print('OK implausible shift fallback')

# New case this refactor must get right: reference == current exactly (genuine zero shift,
# NOT a clamped one) must NOT print the plausibility warning.
img5 = Image.new('RGB', (400, 500), (255, 255, 255))
draw5 = ImageDraw.Draw(img5)
draw5.rectangle([10, 20, 380, 25], fill=(0, 0, 0))
draw5.rectangle([10, 20, 15, 480], fill=(0, 0, 0))
same_anchor = _detect_content_anchor(img5)
ocr_cfg5 = {'boxes': boxes, 'anchor': same_anchor, 'dpi': 150}
result5 = _resolve_crop_boxes(img5, ocr_cfg5)
assert result5 == boxes, result5  # dx=dy=0, adding zero is a no-op, but must reach this line (not the warning branch)
print('OK genuine zero shift (no warning path)')
EOF
python3 -m py_compile cmr_renamer/watcher.py
```

Expected output: five `OK ...` lines, then no output from `py_compile`.

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Extract _compute_anchor_shift from _resolve_crop_boxes for reuse in the calibrator"
```

---

### Task 3: Drift-corrected preview in the calibrator

**Files:**
- Modify: `cmr_renamer/watcher.py` (`_calibra_box`: state dict, `draw_box`, `on_release`,
  `on_file_select`)

**Interfaces:**
- Consumes: `_compute_anchor_shift(current, reference, dpi)` (Task 2), `_detect_content_anchor`
  (already shipped).
- No change to `_calibra_box`'s external signature or return contract
  (`{'boxes': ..., 'anchor': ...} | None`) — this task only changes what's drawn/how drags are
  interpreted internally.

**Note on testability:** identical limitation to the previous two calibrator changes in this repo
— no `tkinter` in this sandbox, so the actual window (drawing, dragging) can't be exercised here.
Verified here: syntax, and the unchanged no-tkinter graceful fallback. Full verification (switching
files shows shifted boxes, dragging on a non-reference file stores the correctly un-shifted
position) requires a machine with `tkinter` (see Task 8).

- [ ] **Step 1: Add `reference_anchor`/`preview_shift` to `state`, seed the reference**

Replace:

```python
    state = {
        'boxes': list(boxes),
        'active': 0, 'start': None, 'drag_id': None, 'result': None,
        'zoom': 1.0, 'photo': None, 'img': None, 'current_path': initial_path,
    }
    drawn_ids: dict = {}
    select_buttons: dict = {}

    try:
        state['img'] = get_image(initial_path)
        root = Tk()
```

with:

```python
    state = {
        'boxes': list(boxes),
        'active': 0, 'start': None, 'drag_id': None, 'result': None,
        'zoom': 1.0, 'photo': None, 'img': None, 'current_path': initial_path,
        'reference_anchor': None, 'preview_shift': (0, 0),
    }
    drawn_ids: dict = {}
    select_buttons: dict = {}

    def update_preview_shift():
        current = _detect_content_anchor(state['img'])
        state['preview_shift'] = _compute_anchor_shift(current, state['reference_anchor'], dpi)

    try:
        state['img'] = get_image(initial_path)
        state['reference_anchor'] = _detect_content_anchor(state['img'])
        # initial_path IS the reference for this session, so its own shift is always (0, 0);
        # update_preview_shift() only needs to run again once a *different* file is selected
        # (see on_file_select below) — no need to call it here too.
        root = Tk()
```

- [ ] **Step 2: Apply the shift when drawing a box**

Replace:

```python
        def draw_box(index):
            scale = total_scale()
            x1, y1, x2, y2 = [c * scale for c in state['boxes'][index]]
            if index in drawn_ids:
                canvas.delete(drawn_ids[index])
            drawn_ids[index] = canvas.create_rectangle(
                x1, y1, x2, y2, outline=BOX_COLORS[index], width=3
            )
```

with:

```python
        def draw_box(index):
            scale = total_scale()
            dx, dy = state['preview_shift']
            bx1, by1, bx2, by2 = state['boxes'][index]
            x1, y1, x2, y2 = [(bx1 + dx) * scale, (by1 + dy) * scale, (bx2 + dx) * scale, (by2 + dy) * scale]
            if index in drawn_ids:
                canvas.delete(drawn_ids[index])
            drawn_ids[index] = canvas.create_rectangle(
                x1, y1, x2, y2, outline=BOX_COLORS[index], width=3
            )
```

- [ ] **Step 3: Un-shift the position when a drag finishes**

Replace:

```python
            index = state['active']
            state['boxes'][index] = (int(x0 / scale), int(y0 / scale), int(x1 / scale), int(y1 / scale))
            draw_box(index)
```

with:

```python
            dx, dy = state['preview_shift']
            index = state['active']
            state['boxes'][index] = (
                int(x0 / scale) - dx, int(y0 / scale) - dy,
                int(x1 / scale) - dx, int(y1 / scale) - dy,
            )
            draw_box(index)
```

- [ ] **Step 4: Recompute the shift on file switch**

Replace:

```python
            state['img'] = new_img
            state['current_path'] = path
            render()

        file_listbox.bind("<<ListboxSelect>>", on_file_select)
```

with:

```python
            state['img'] = new_img
            state['current_path'] = path
            update_preview_shift()
            render()

        file_listbox.bind("<<ListboxSelect>>", on_file_select)
```

- [ ] **Step 5: Verify what's checkable without `tkinter`**

```bash
python3 -m py_compile cmr_renamer/watcher.py
python3 -c "
import sys
sys.path.insert(0, '.')
from cmr_renamer import watcher
result = watcher._calibra_box(['/tmp/a.pdf'], '/tmp/a.pdf', [(0,0,10,10),(0,0,10,10)], 150)
assert result is None
print('OK: still returns None gracefully with tkinter unavailable')
"
```

Expected output: `OK: still returns None gracefully with tkinter unavailable`.

- [ ] **Step 6: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Show drift-corrected box preview when browsing files in the calibrator"
```

---

### Task 4: Pure validation helpers for the GUI setup form

**Files:**
- Modify: `cmr_renamer/config.py` (add helpers near the top, after imports)

**Interfaces:**
- Produces: `_parse_positive_int(value: str) -> int | None` — `None` if `value` isn't a valid
  positive integer.
- Produces: `_build_lang_string(eng: bool, ita: bool, deu: bool, extra: str) -> str` — joins
  selected codes and any `+`-separated extra codes with `+`, e.g. `"eng+ita"`, `"eng+fra"`, or `""`
  if nothing is selected/typed.

- [ ] **Step 1: Add the helpers**

Insert immediately after the `_prompt_console` function (before `_prompt_for_folder`):

```python
def _parse_positive_int(value: str) -> "int | None":
    """Converte una stringa in intero positivo, o None se non valida."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _build_lang_string(eng: bool, ita: bool, deu: bool, extra: str) -> str:
    """Unisce le lingue selezionate (checkbox) e il testo extra in una stringa tipo 'eng+ita'."""
    codes = []
    if eng:
        codes.append('eng')
    if ita:
        codes.append('ita')
    if deu:
        codes.append('deu')
    extra_codes = [c.strip() for c in extra.split('+') if c.strip()]
    codes.extend(extra_codes)
    return '+'.join(codes)
```

- [ ] **Step 2: Verify**

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from cmr_renamer.config import _parse_positive_int, _build_lang_string

assert _parse_positive_int("300") == 300
assert _parse_positive_int("0") is None
assert _parse_positive_int("-5") is None
assert _parse_positive_int("abc") is None
assert _parse_positive_int("") is None
assert _parse_positive_int(" 12 ") == 12
print('OK _parse_positive_int')

assert _build_lang_string(True, False, False, "") == "eng"
assert _build_lang_string(True, True, False, "") == "eng+ita"
assert _build_lang_string(False, False, False, "") == ""
assert _build_lang_string(True, False, False, "fra") == "eng+fra"
assert _build_lang_string(False, False, False, "  spa + kor ") == "spa+kor"
assert _build_lang_string(True, True, True, "fra") == "eng+ita+deu+fra"
print('OK _build_lang_string')
EOF
python3 -m py_compile cmr_renamer/config.py
```

Expected output: `OK _parse_positive_int` then `OK _build_lang_string`, then no output from
`py_compile`.

- [ ] **Step 3: Commit**

```bash
git add cmr_renamer/config.py
git commit -m "Add pure validation helpers for the GUI setup form"
```

---

### Task 5: The GUI setup form itself

**Files:**
- Modify: `cmr_renamer/config.py` (tkinter import block; new `_prompt_with_gui` function)

**Interfaces:**
- Consumes: `_parse_positive_int`, `_build_lang_string` (Task 4), `TKINTER_AVAILABLE`,
  `askdirectory` (already imported).
- Produces: `_prompt_with_gui() -> dict | None` — on save, a dict with string keys `folder`,
  `prefix`, `delay_riavvio`, `lang`, `dpi`, `max_length`, `remove_leading_zeros` (matching exactly
  what `load_or_create_config` writes into `config.ini` today); `None` if `tkinter` is unavailable,
  the window errors, or the user closes it without saving.

**Note on testability:** this sandbox has no `tkinter`, so `_prompt_with_gui()` here immediately
takes the `if not TKINTER_AVAILABLE: return None` branch and never reaches the window-building
code — that part needs manual verification (see Task 8). Step 2 below confirms that early-return
path.

- [ ] **Step 1: Extend the tkinter import block**

Replace:

```python
try:
    from tkinter import Tk
    from tkinter.filedialog import askdirectory
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
```

with:

```python
try:
    from tkinter import Tk, Frame, Label, Entry, Button, Checkbutton, BooleanVar, StringVar
    from tkinter.filedialog import askdirectory
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
```

- [ ] **Step 2: Add `_prompt_with_gui`**

Insert after `_prompt_for_folder` (before `load_or_create_config`):

```python
def _prompt_with_gui() -> "dict | None":
    """Mostra un'unica finestra Tkinter con tutti i campi di configurazione iniziale.

    Ritorna un dict con le stesse chiavi che load_or_create_config scrive in config.ini (tutti
    valori stringa), oppure None se tkinter non è disponibile o la finestra viene chiusa senza
    salvare — in quel caso il chiamante ricade sui prompt a console.
    """
    if not TKINTER_AVAILABLE:
        return None

    result = {'value': None}

    try:
        root = Tk()
        root.title("CMR Renamer - Configurazione Iniziale")

        folder_var = StringVar(value="")
        prefix_var = StringVar(value="DOC")
        delay_var = StringVar(value="3")
        dpi_var = StringVar(value="300")
        max_length_var = StringVar(value="60")
        extra_lang_var = StringVar(value="")
        eng_var = BooleanVar(value=True)
        ita_var = BooleanVar(value=False)
        deu_var = BooleanVar(value=False)
        remove_zeros_var = BooleanVar(value=True)

        def browse_folder():
            chosen = askdirectory(title="Seleziona la cartella da monitorare")
            if chosen:
                folder_var.set(chosen)

        row = 0
        Label(root, text="Cartella da monitorare:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=folder_var, width=40).grid(row=row, column=1, padx=8, pady=4)
        Button(root, text="Sfoglia...", command=browse_folder).grid(row=row, column=2, padx=8, pady=4)
        row += 1

        Label(root, text="Prefisso file (es. DOC):").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=prefix_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="Delay tra rilevamenti (secondi):").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=delay_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="Lingua OCR:").grid(row=row, column=0, sticky="nw", padx=8, pady=4)
        lang_frame = Frame(root)
        lang_frame.grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        Checkbutton(lang_frame, text="eng", variable=eng_var).pack(side="left")
        Checkbutton(lang_frame, text="ita", variable=ita_var).pack(side="left")
        Checkbutton(lang_frame, text="deu", variable=deu_var).pack(side="left")
        row += 1

        Label(root, text="Altre lingue (es. fra, separate da +):").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=extra_lang_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="DPI per conversione PDF:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=dpi_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Label(root, text="Lunghezza massima per parte del nome file:").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        Entry(root, textvariable=max_length_var, width=40).grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        row += 1

        Checkbutton(root, text="Rimuovi zeri iniziali dai numeri", variable=remove_zeros_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=4
        )
        row += 1

        error_label = Label(root, text="", fg="red")
        error_label.grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        row += 1

        def on_confirm():
            delay = _parse_positive_int(delay_var.get())
            dpi = _parse_positive_int(dpi_var.get())
            max_length = _parse_positive_int(max_length_var.get())
            lang = _build_lang_string(eng_var.get(), ita_var.get(), deu_var.get(), extra_lang_var.get())

            if delay is None:
                error_label.config(text="Il delay tra rilevamenti deve essere un numero intero positivo.")
                return
            if dpi is None:
                error_label.config(text="Il DPI deve essere un numero intero positivo.")
                return
            if max_length is None:
                error_label.config(text="La lunghezza massima del nome deve essere un numero intero positivo.")
                return
            if not lang:
                error_label.config(text="Seleziona almeno una lingua OCR.")
                return

            result['value'] = {
                'folder': folder_var.get(),
                'prefix': prefix_var.get() or "DOC",
                'delay_riavvio': str(delay),
                'lang': lang,
                'dpi': str(dpi),
                'max_length': str(max_length),
                'remove_leading_zeros': str(remove_zeros_var.get()),
            }
            root.destroy()

        def on_close():
            result['value'] = None
            root.destroy()

        Button(root, text="Conferma", command=on_confirm).grid(row=row, column=0, columnspan=3, pady=8)
        root.protocol("WM_DELETE_WINDOW", on_close)

        root.mainloop()
        return result['value']
    except Exception as e:
        print(f"⚠️ Errore nella finestra di configurazione: {e}. Uso i prompt da console.")
        return None
```

- [ ] **Step 3: Verify what's checkable without `tkinter`**

```bash
python3 -c "
import sys
sys.path.insert(0, '.')
from cmr_renamer.config import _prompt_with_gui, TKINTER_AVAILABLE
assert TKINTER_AVAILABLE is False
assert _prompt_with_gui() is None
print('OK: graceful no-tkinter fallback')
"
python3 -m py_compile cmr_renamer/config.py
```

Expected output: `OK: graceful no-tkinter fallback`, then no output from `py_compile`.

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/config.py
git commit -m "Add _prompt_with_gui: Tkinter form for initial configuration"
```

---

### Task 6: Wire the GUI form into setup, skip console allocation when possible

**Files:**
- Modify: `cmr_renamer/config.py` (`load_or_create_config`)
- Modify: `cmr_renamer/watcher.py` (`run()`'s frozen-with-no-config branch)

**Interfaces:**
- Consumes: `_prompt_with_gui()` (Task 5), `TKINTER_AVAILABLE` (watcher.py's own module-level flag,
  already defined for the calibrator).

- [ ] **Step 1: Wire `_prompt_with_gui` into `load_or_create_config`**

Replace:

```python
    if not os.path.exists(config_path):
        print("\n=== FILE DI CONFIGURAZIONE NON TROVATO ===")
        print(f"Percorso atteso: {config_path}")
        print("Creazione guidata — Invio per usare il valore predefinito tra [parentesi].\n")

        config['Watcher'] = {}
        config['Watcher']['folder'] = _prompt_for_folder()
        config['Watcher']['prefix'] = _prompt_console(
            "Prefisso del nome file da cercare (es. DOC)", "DOC"
        )
        config['Watcher']['delay_riavvio'] = _prompt_console(
            "Delay tra rilevamenti ripetuti (secondi)", "3"
        )

        config['OCR'] = {}
        config['OCR']['lang'] = _prompt_console("Lingua OCR (eng/ita/deu/fra)", "eng")
        config['OCR']['dpi'] = _prompt_console("DPI per conversione PDF", "300")
        # box1..box5 are intentionally not asked here: they have no sensible
        # generic default and are selected with the mouse on the first PDF
        # that gets processed (see watcher._rinomina_pdf / _calibra_box).

        config['Filename'] = {}
        config['Filename']['max_length'] = _prompt_console(
            "Lunghezza massima per parte del nome file", "60"
        )
        remove_zeros = _prompt_console(
            "Rimuovere zeri iniziali dai numeri? (True/False)", "True"
        ).lower()
        config['Filename']['remove_leading_zeros'] = str(remove_zeros in ['true', '1', 'yes', 'y'])

        with open(config_path, 'w') as f:
            config.write(f)
        print(f"\n✅ Configurazione salvata in: {config_path}\n")
    else:
        config.read(config_path)
        print(f"📂 Configurazione caricata da: {config_path}")

    return config
```

with:

```python
    if not os.path.exists(config_path):
        print("\n=== FILE DI CONFIGURAZIONE NON TROVATO ===")
        print(f"Percorso atteso: {config_path}")

        gui_values = _prompt_with_gui()

        if gui_values is not None:
            config['Watcher'] = {}
            config['Watcher']['folder'] = gui_values['folder']
            config['Watcher']['prefix'] = gui_values['prefix']
            config['Watcher']['delay_riavvio'] = gui_values['delay_riavvio']

            config['OCR'] = {}
            config['OCR']['lang'] = gui_values['lang']
            config['OCR']['dpi'] = gui_values['dpi']

            config['Filename'] = {}
            config['Filename']['max_length'] = gui_values['max_length']
            config['Filename']['remove_leading_zeros'] = gui_values['remove_leading_zeros']
        else:
            print("Creazione guidata — Invio per usare il valore predefinito tra [parentesi].\n")

            config['Watcher'] = {}
            config['Watcher']['folder'] = _prompt_for_folder()
            config['Watcher']['prefix'] = _prompt_console(
                "Prefisso del nome file da cercare (es. DOC)", "DOC"
            )
            config['Watcher']['delay_riavvio'] = _prompt_console(
                "Delay tra rilevamenti ripetuti (secondi)", "3"
            )

            config['OCR'] = {}
            config['OCR']['lang'] = _prompt_console("Lingua OCR (eng/ita/deu/fra)", "eng")
            config['OCR']['dpi'] = _prompt_console("DPI per conversione PDF", "300")
            # box1..box5 are intentionally not asked here: they have no sensible
            # generic default and are selected with the mouse on the first PDF
            # that gets processed (see watcher._rinomina_pdf / _calibra_box).

            config['Filename'] = {}
            config['Filename']['max_length'] = _prompt_console(
                "Lunghezza massima per parte del nome file", "60"
            )
            remove_zeros = _prompt_console(
                "Rimuovere zeri iniziali dai numeri? (True/False)", "True"
            ).lower()
            config['Filename']['remove_leading_zeros'] = str(remove_zeros in ['true', '1', 'yes', 'y'])

        with open(config_path, 'w') as f:
            config.write(f)
        print(f"\n✅ Configurazione salvata in: {config_path}\n")
    else:
        config.read(config_path)
        print(f"📂 Configurazione caricata da: {config_path}")

    return config
```

- [ ] **Step 2: Verify the fallback path is unchanged (no `tkinter` in this sandbox, so
      `_prompt_with_gui` always returns `None` here — this exercises the `else` branch exactly as
      before)**

```bash
python3 - <<'EOF'
import sys, tempfile, os, io
sys.path.insert(0, '.')
from cmr_renamer import config as config_module

with tempfile.TemporaryDirectory() as d:
    config_path = os.path.join(d, 'config.ini')
    inputs = iter([
        '/watched/folder',  # folder (console fallback, tkinter unavailable here)
        '',   # prefix -> default DOC
        '',   # delay -> default 3
        '',   # lang -> default eng
        '',   # dpi -> default 300
        '',   # max_length -> default 60
        '',   # remove_leading_zeros -> default True
    ])
    config_module.input = lambda *a, **k: next(inputs)
    cfg = config_module.load_or_create_config(config_path=config_path)
    assert cfg['Watcher']['folder'] == '/watched/folder', dict(cfg['Watcher'])
    assert cfg['Watcher']['prefix'] == 'DOC'
    assert cfg['OCR']['lang'] == 'eng'
    assert cfg['Filename']['remove_leading_zeros'] == 'True'
    print('OK console fallback unchanged', dict(cfg['Watcher']), dict(cfg['OCR']), dict(cfg['Filename']))
EOF
python3 -m py_compile cmr_renamer/config.py
```

Expected output: `OK console fallback unchanged ...`, then no output from `py_compile`.

- [ ] **Step 3: Verify the GUI path writes what the form returns (monkeypatches `_prompt_with_gui`
      directly — this is the same technique used for `_calibra_box` in earlier plans)**

```bash
python3 - <<'EOF'
import sys, tempfile, os, configparser
sys.path.insert(0, '.')
from cmr_renamer import config as config_module

config_module._prompt_with_gui = lambda: {
    'folder': '/watched/folder',
    'prefix': 'DOC',
    'delay_riavvio': '3',
    'lang': 'eng+ita',
    'dpi': '300',
    'max_length': '60',
    'remove_leading_zeros': 'True',
}

with tempfile.TemporaryDirectory() as d:
    config_path = os.path.join(d, 'config.ini')
    cfg = config_module.load_or_create_config(config_path=config_path)
    assert cfg['OCR']['lang'] == 'eng+ita', dict(cfg['OCR'])

    saved = configparser.ConfigParser()
    saved.read(config_path)
    assert saved['Watcher']['folder'] == '/watched/folder', dict(saved['Watcher'])
    assert saved['OCR']['lang'] == 'eng+ita', dict(saved['OCR'])
    print('OK GUI path writes config.ini correctly', dict(saved['Watcher']), dict(saved['OCR']))
EOF
```

Expected output: `OK GUI path writes config.ini correctly ...`.

- [ ] **Step 4: Update `run()` to skip console allocation when `tkinter` is available**

In `cmr_renamer/watcher.py`, replace:

```python
    # ── Background vs interactive mode ─────────────────────
    if _is_frozen() and not config_exists:
        # Frozen + no config → allocate console for setup
        _alloc_console()
        print("\n=== CMR Renamer - Configurazione Iniziale ===\n")
        try:
            cfg = load_or_create_config(config_path=config_path)
        except Exception as e:
            print(f"❌ Errore durante la configurazione: {e}")
            _free_console()
            return 1 # Indicate error
        else:
            # No exception
            _free_console()
            # After setup, continue in background mode
            _setup_file_logging(config_dir)
    elif _is_frozen() and config_exists:
```

with:

```python
    # ── Background vs interactive mode ─────────────────────
    if _is_frozen() and not config_exists and not TKINTER_AVAILABLE:
        # Frozen + no config + no tkinter → allocate a console so the text
        # prompts are visible, since there's no GUI form to fall back to.
        _alloc_console()
        print("\n=== CMR Renamer - Configurazione Iniziale ===\n")
        try:
            cfg = load_or_create_config(config_path=config_path)
        except Exception as e:
            print(f"❌ Errore durante la configurazione: {e}")
            _free_console()
            return 1 # Indicate error
        else:
            # No exception
            _free_console()
            # After setup, continue in background mode
            _setup_file_logging(config_dir)
    elif _is_frozen() and not config_exists:
        # Frozen + no config + tkinter available → the GUI form needs no console.
        # File logging must be set up BEFORE load_or_create_config runs: a
        # --windowed build with no console allocated has sys.stdout/stderr set to
        # None, and load_or_create_config prints status messages — those would
        # raise AttributeError without somewhere to write to first.
        _setup_file_logging(config_dir)
        try:
            cfg = load_or_create_config(config_path=config_path)
        except Exception as e:
            print(f"❌ Errore durante la configurazione: {e}")
            return 1 # Indicate error
    elif _is_frozen() and config_exists:
```

- [ ] **Step 5: Full-file syntax check**

```bash
python3 -m py_compile cmr_renamer/watcher.py
```

Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add cmr_renamer/config.py cmr_renamer/watcher.py
git commit -m "Wire GUI setup form into load_or_create_config, skip console when tkinter available"
```

---

### Task 7: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the frozen-execution-mode bullet list**

Replace:

```markdown
- Frozen + no `config.ini`: allocates a Windows console (`_alloc_console`/`_free_console`, via
  `ctypes.windll.kernel32`) so the interactive setup prompts are visible, then frees it and switches
  to file logging (`_setup_file_logging` redirects `sys.stdout`/`stderr` to `cmr-renamer.log`) for
  normal background operation.
```

with:

```markdown
- Frozen + no `config.ini`, `tkinter` unavailable: allocates a Windows console
  (`_alloc_console`/`_free_console`, via `ctypes.windll.kernel32`) so the interactive console
  setup prompts are visible, then frees it and switches to file logging (`_setup_file_logging`
  redirects `sys.stdout`/`stderr` to `cmr-renamer.log`) for normal background operation.
- Frozen + no `config.ini`, `tkinter` available: no console at all — `_setup_file_logging` runs
  first (a `--windowed` build with no console has `sys.stdout`/`stderr` as `None`, so anything
  printed before logging is set up would crash), then `load_or_create_config` shows the GUI setup
  form directly.
```

- [ ] **Step 2: Update the config-creation paragraph**

Replace:

```markdown
**Config lives next to the executable (or CWD when running from source)**, in `config.ini`, and is
created interactively on first run by `config.py` (`load_or_create_config`). It prefers a tkinter
folder picker for the watched directory, falling back to console input if tkinter/GUI is unavailable.
```

with:

```markdown
**Config lives next to the executable (or CWD when running from source)**, in `config.ini`, and is
created interactively on first run by `config.py` (`load_or_create_config`). It prefers a single
Tkinter form (`_prompt_with_gui`) covering every setting — folder (with a "Sfoglia..." button
reusing the same `askdirectory` picker), prefix, delay, OCR language (checkboxes for the bundled
`eng`/`ita`/`deu` plus a free-text field for extra codes, joined with `+` via `_build_lang_string`),
dpi, max filename length, and the leading-zeros checkbox — validating delay/dpi/max_length as
positive integers (`_parse_positive_int`) and requiring at least one language before saving.
Falls back to the original per-field console prompts if `tkinter` is unavailable or the form is
closed without saving.
```

- [ ] **Step 3: Verify**

```bash
grep -n "_prompt_with_gui\|_alloc_console.*_free_console.*ctypes" CLAUDE.md
```

Expected: both the new GUI-form mention and the (now console-only-branch) console mention appear.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Document GUI setup form and calibrator drift preview in CLAUDE.md"
```

---

### Task 8: Manual verification (human, on a machine with `tkinter`)

**Files:** none — this is a checklist, not a code change.

- [ ] In the box calibrator, with a saved anchor already in `config.ini`, open "Ricalibra box" (or
      trigger mandatory calibration) against a folder with 2+ PDFs, and confirm: the file that
      opens the calibrator shows boxes at their raw calibrated position; switching to another file
      in the sidebar visibly shifts the box overlay to the corrected position; switching back shows
      the raw position again.
- [ ] Drag a box while a *non-reference* file is displayed, save, then reopen the calibrator and
      confirm the box lands correctly on the reference file too (i.e. the stored coordinate was
      correctly un-shifted, not accidentally double-shifted).
- [ ] Delete `config.ini` and start the app (both from source and, if possible, the built `.exe`):
      confirm the GUI setup form opens (no console flash on the frozen build), all fields have the
      documented defaults, "Sfoglia..." opens the folder picker, and entering a non-numeric delay/
      dpi/max_length or unchecking all languages with no extra text shows the inline error and
      blocks saving.
- [ ] Confirm a `config.ini` produced by the GUI form is byte-for-byte compatible with one produced
      by the console fallback (same keys/sections) — start the app once via each path and diff the
      resulting `config.ini`s (minus the actual chosen values).
- [ ] From the tray, with a UNC network folder configured (e.g. `\\server\share\folder` or the
      forward-slash form Tkinter's picker produces), click "Apri cartella monitorata" and confirm
      Explorer opens the folder instead of failing with `WinError 2`.

- [ ] **Report results back** (pass/fail per bullet) before merging/shipping this change.
