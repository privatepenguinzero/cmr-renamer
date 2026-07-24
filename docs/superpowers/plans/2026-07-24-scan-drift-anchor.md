# Scan Drift Correction via Content Anchor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically shift calibrated OCR boxes to compensate for small scan-to-scan drift (vertical and/or horizontal) by anchoring them to where the page content ("black") starts, instead of relying on raw absolute page coordinates.

**Architecture:** A new pure-PIL helper `_detect_content_anchor` finds the top-left edge of non-white content on a page. The calibrator records that position (from whatever page is on screen at save time) as the reference anchor alongside the boxes. Every time a file is processed, `_rinomina_pdf` re-detects the anchor on that page and shifts the calibrated boxes by the difference before cropping — falling back to the unmodified boxes (with a warning) whenever detection fails or the shift looks implausible.

**Tech Stack:** Python, Pillow only (`Image.resize` with `BOX` resampling for fast row/column darkness profiles) — no numpy, no OpenCV, no new dependency.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-scan-drift-anchor-design.md`.
- No automated test suite exists in this repo (per `CLAUDE.md`) — verification below uses ad-hoc
  Python scripts run via `python3`, not `pytest`. Do not add a `pytest` dependency or a `tests/`
  directory.
- PIL-only implementation. Do not add numpy, OpenCV, or any other image-processing dependency.
- Translation-only correction (X and Y shift). Do not implement rotation/deskew correction.
- Detection constants (dark threshold, minimum dark fraction, minimum run length, max plausible
  shift) are fixed internal constants, not exposed in `config.ini` or anywhere configurable.
- The feature is always active once a reference anchor exists in `config.ini` — no on/off toggle.
- `anchor_x`/`anchor_y` in `config.ini` follow the same optional-key pattern as `box1..5` /
  `show_rects` — absent in old configs, feature silently inactive until the next successful
  calibration.
- This sandbox has no `tkinter` installed, so the interactive calibrator window cannot be opened
  or clicked through here — call this out explicitly wherever a step can't be exercised end-to-end;
  do not claim GUI behavior was verified when it wasn't.

---

### Task 1: `_detect_content_anchor` — content anchor detection

**Files:**
- Modify: `cmr_renamer/watcher.py` (insert after `_preprocess_for_ocr`, before `_file_pronto`)

**Interfaces:**
- Produces: `_detect_content_anchor(img: Image.Image) -> tuple[int, int] | None` — `(anchor_x,
  anchor_y)` in pixel space, or `None` if no sustained dark row/column is found (near-blank page).
  Also introduces `_content_profile` and `_find_content_start` as internal helpers used only by
  `_detect_content_anchor`.

- [ ] **Step 1: Add the constants and functions**

Insert immediately after `_preprocess_for_ocr` (the function ending with
`return contrasted.point(lambda p: 255 if p > 128 else 0)`) and before `def _file_pronto(...)`:

```python
_ANCHOR_DARK_THRESHOLD = 128  # stessa soglia di _preprocess_for_ocr
_ANCHOR_MIN_DARK_FRACTION = 0.03  # frazione minima di pixel scuri per considerare una riga/colonna "contenuto"
_ANCHOR_MIN_RUN = 4  # posizioni consecutive richieste, per ignorare rumore isolato (graffette, polvere)


def _content_profile(binaria: "Image.Image", axis_size: int, vertical: bool) -> list:
    """Frazione di pixel scuri per riga (vertical=True) o per colonna, via resize con filtro BOX."""
    if vertical:
        small = binaria.resize((1, axis_size), Image.BOX)
    else:
        small = binaria.resize((axis_size, 1), Image.BOX)
    return [(255 - p) / 255 for p in small.getdata()]


def _find_content_start(profile: list) -> "int | None":
    """Primo indice con densità di scuro sufficiente, sostenuta per _ANCHOR_MIN_RUN posizioni."""
    run = 0
    for i, frac in enumerate(profile):
        if frac >= _ANCHOR_MIN_DARK_FRACTION:
            run += 1
            if run >= _ANCHOR_MIN_RUN:
                return i - _ANCHOR_MIN_RUN + 1
        else:
            run = 0
    return None


def _detect_content_anchor(img: "Image.Image") -> "tuple[int, int] | None":
    """Rileva dove inizia il contenuto (non bianco) dall'alto e da sinistra della pagina.

    Ritorna (anchor_x, anchor_y) in pixel, o None se non trova un bordo di contenuto sostenuto
    (pagina quasi bianca).
    """
    gray = img.convert('L')
    binaria = gray.point(lambda p: 0 if p < _ANCHOR_DARK_THRESHOLD else 255)
    w, h = binaria.size

    anchor_y = _find_content_start(_content_profile(binaria, h, vertical=True))
    anchor_x = _find_content_start(_content_profile(binaria, w, vertical=False))

    if anchor_x is None or anchor_y is None:
        return None
    return (anchor_x, anchor_y)
```

- [ ] **Step 2: Verify**

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
from cmr_renamer.watcher import _detect_content_anchor

img = Image.new('RGB', (800, 1100), (255, 255, 255))
draw = ImageDraw.Draw(img)
draw.rectangle([50, 100, 750, 105], fill=(0, 0, 0))  # bordo superiore tabella
draw.rectangle([50, 100, 55, 900], fill=(0, 0, 0))   # bordo sinistro tabella
anchor = _detect_content_anchor(img)
assert anchor is not None
ax, ay = anchor
assert 45 <= ax <= 55, anchor
assert 95 <= ay <= 105, anchor
print('OK', anchor)

blank = Image.new('RGB', (800, 1100), (255, 255, 255))
assert _detect_content_anchor(blank) is None
print('OK blank page -> None')
EOF
```

Expected output: `OK (<ax>, <ay>)` then `OK blank page -> None`.

- [ ] **Step 3: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add _detect_content_anchor for scan drift detection"
```

---

### Task 2: Config storage — `_load_anchor_from_config` and `_save_calibration_to_config`

**Files:**
- Modify: `cmr_renamer/watcher.py` (add loader after `_load_boxes_from_config`; rename and extend
  `_save_boxes_to_config`)

**Interfaces:**
- Produces: `_load_anchor_from_config(ocr_section) -> tuple[int, int] | None`.
- Produces: `_save_calibration_to_config(boxes: list, anchor: tuple[int, int] | None) -> None`
  (replaces `_save_boxes_to_config(boxes)` — same box-writing behavior, plus writes/removes
  `anchor_x`/`anchor_y`).

- [ ] **Step 1: Add `_load_anchor_from_config`**

Insert immediately after `_load_boxes_from_config` (the function ending with `return boxes`) and
before `BOX_COLORS = [...]`:

```python
def _load_anchor_from_config(ocr_section) -> "tuple[int, int] | None":
    """Legge anchor_x/anchor_y da una sezione [OCR] già caricata, se entrambi presenti."""
    x = ocr_section.get('anchor_x')
    y = ocr_section.get('anchor_y')
    if x is None or y is None:
        return None
    return (int(x), int(y))
```

- [ ] **Step 2: Rename and extend the save function**

Replace:

```python
def _save_boxes_to_config(boxes: list) -> None:
    """Salva le coordinate di tutti i box (2-5) nel config.ini esistente."""
    config_path = os.path.join(_get_config_dir(), 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path)
    if 'OCR' not in config:
        config['OCR'] = {}
    for i, box in enumerate(boxes, start=1):
        config['OCR'][f'box{i}'] = ','.join(str(int(v)) for v in box)
    # Rimuove eventuali chiavi box(N+1).. rimaste da una configurazione precedente
    # con più box (es. da 4 box a 3: box4 va eliminato, non lasciato stantio).
    for i in range(len(boxes) + 1, MAX_BOXES + 1):
        config['OCR'].pop(f'box{i}', None)
    with open(config_path, 'w') as f:
        config.write(f)
```

with:

```python
def _save_calibration_to_config(boxes: list, anchor: "tuple[int, int] | None") -> None:
    """Salva le coordinate di tutti i box (2-5) e l'ancora di contenuto nel config.ini esistente."""
    config_path = os.path.join(_get_config_dir(), 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path)
    if 'OCR' not in config:
        config['OCR'] = {}
    for i, box in enumerate(boxes, start=1):
        config['OCR'][f'box{i}'] = ','.join(str(int(v)) for v in box)
    # Rimuove eventuali chiavi box(N+1).. rimaste da una configurazione precedente
    # con più box (es. da 4 box a 3: box4 va eliminato, non lasciato stantio).
    for i in range(len(boxes) + 1, MAX_BOXES + 1):
        config['OCR'].pop(f'box{i}', None)
    if anchor is not None:
        config['OCR']['anchor_x'] = str(int(anchor[0]))
        config['OCR']['anchor_y'] = str(int(anchor[1]))
    else:
        config['OCR'].pop('anchor_x', None)
        config['OCR'].pop('anchor_y', None)
    with open(config_path, 'w') as f:
        config.write(f)
```

- [ ] **Step 3: Verify**

```bash
python3 - <<'EOF'
import sys, tempfile, os, configparser
sys.path.insert(0, '.')
from cmr_renamer import watcher

with tempfile.TemporaryDirectory() as d:
    config_path = os.path.join(d, 'config.ini')
    with open(config_path, 'w') as f:
        f.write("[OCR]\nlang = eng\ndpi = 300\n")

    watcher._get_config_dir = lambda: d

    watcher._save_calibration_to_config([(1, 2, 3, 4), (5, 6, 7, 8)], (42, 99))
    cfg = configparser.ConfigParser()
    cfg.read(config_path)
    assert cfg['OCR']['anchor_x'] == '42', dict(cfg['OCR'])
    assert cfg['OCR']['anchor_y'] == '99', dict(cfg['OCR'])
    assert watcher._load_anchor_from_config(cfg['OCR']) == (42, 99)

    # Salvare senza ancora deve rimuovere le chiavi
    watcher._save_calibration_to_config([(1, 2, 3, 4)], None)
    cfg2 = configparser.ConfigParser()
    cfg2.read(config_path)
    assert 'anchor_x' not in cfg2['OCR'], dict(cfg2['OCR'])
    assert watcher._load_anchor_from_config(cfg2['OCR']) is None
    print('OK')
EOF
```

Expected output: `OK`

- [ ] **Step 4: Full-file syntax check**

```bash
python3 -m py_compile cmr_renamer/watcher.py
```

Expected: no output, exit code 0. (This will still fail at this point only if Step 2's replace
missed a caller — callers are updated in Task 4; `_save_boxes_to_config` has no other definition,
so nothing else references the old name yet except the two call sites fixed in Task 4. Running
`py_compile` here just confirms the file's own syntax; the stale call sites still referencing
`_save_boxes_to_config` won't raise a *syntax* error, only a `NameError` at runtime — that's
resolved in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add anchor config loader, rename _save_boxes_to_config to _save_calibration_to_config"
```

---

### Task 3: Compute the anchor at calibration save time

**Files:**
- Modify: `cmr_renamer/watcher.py` (`_calibra_box`'s docstring and `on_save`)

**Interfaces:**
- Consumes: `_detect_content_anchor(img)` (Task 1).
- Produces: `_calibra_box(...)` now returns `{'boxes': list, 'anchor': tuple | None}` on save
  (still `None` on cancel or if `tkinter` is unavailable) — this is a breaking change to the
  previous `list | None` contract, fixed up in Task 4.

**Note on testability:** as with the calibrator's sidebar feature, this sandbox has no `tkinter`,
so `on_save`'s body (only reachable from inside a real Tk mainloop) cannot be exercised here.
`_detect_content_anchor` itself is already verified in Task 1; this step only wires it into a
one-line dict construction. Full confirmation that saving actually stores a sensible anchor
requires a manual check on a machine with `tkinter` (see Task 8).

- [ ] **Step 1: Update the docstring**

Replace:

```python
    tuple (x1,y1,x2,y2). Ritorna la nuova lista di box se l'utente salva, altrimenti None.
    """
```

with:

```python
    tuple (x1,y1,x2,y2). Se l'utente salva, ritorna {'boxes': [...], 'anchor': (x,y) | None} —
    l'ancora è rilevata sull'immagine visualizzata al momento del salvataggio (non necessariamente
    quella di `initial_path`, se nel frattempo si è passati a un altro file dalla lista). Ritorna
    None se l'utente annulla.
    """
```

- [ ] **Step 2: Update `on_save`**

Replace:

```python
        def on_save():
            state['result'] = list(state['boxes'])
            root.destroy()
```

with:

```python
        def on_save():
            state['result'] = {'boxes': list(state['boxes']), 'anchor': _detect_content_anchor(state['img'])}
            root.destroy()
```

- [ ] **Step 3: Verify what's checkable without `tkinter`**

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

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Compute content anchor at calibration save time"
```

---

### Task 4: Wire the new return contract into both calibration call sites

**Files:**
- Modify: `cmr_renamer/watcher.py` (`_rinomina_pdf`'s mandatory-calibration branch; `_recalibra`
  inside `_build_tray_icon`)

**Interfaces:**
- Consumes: `_calibra_box(...) -> {'boxes': list, 'anchor': tuple | None} | None` (Task 3),
  `_save_calibration_to_config(boxes, anchor)` (Task 2).
- Produces: both call sites now keep `ocr_cfg['anchor']` in sync with the persisted config.

- [ ] **Step 1: Update `_rinomina_pdf`'s calibration branch**

Replace:

```python
                    pdf_paths = _list_watched_pdfs(os.path.dirname(pdf_path))
                    nuovi_box = _calibra_box(pdf_paths, pdf_path, boxes_seed, ocr_cfg['dpi'])
                    if nuovi_box:
                        ocr_cfg['boxes'] = nuovi_box
                        _save_boxes_to_config(ocr_cfg['boxes'])
                        print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
                    elif serve_calibrazione:
```

with:

```python
                    pdf_paths = _list_watched_pdfs(os.path.dirname(pdf_path))
                    risultato = _calibra_box(pdf_paths, pdf_path, boxes_seed, ocr_cfg['dpi'])
                    if risultato:
                        ocr_cfg['boxes'] = risultato['boxes']
                        ocr_cfg['anchor'] = risultato['anchor']
                        _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'])
                        print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
                    elif serve_calibrazione:
```

- [ ] **Step 2: Update the tray's `_recalibra`**

Replace:

```python
            nuovi_box = _calibra_box(pdf_paths, pdf_paths[0], boxes_seed, ocr_cfg['dpi'])
            if nuovi_box:
                ocr_cfg['boxes'] = nuovi_box
                _save_boxes_to_config(ocr_cfg['boxes'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
```

with:

```python
            risultato = _calibra_box(pdf_paths, pdf_paths[0], boxes_seed, ocr_cfg['dpi'])
            if risultato:
                ocr_cfg['boxes'] = risultato['boxes']
                ocr_cfg['anchor'] = risultato['anchor']
                _save_calibration_to_config(ocr_cfg['boxes'], ocr_cfg['anchor'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
```

- [ ] **Step 3: Verify the `_rinomina_pdf` wiring**

This monkeypatches `_calibra_box` to return the new dict shape and `pytesseract.image_to_string`
to avoid needing a real `tesseract` binary (not installed in this sandbox) — it only needs to
prove the calibration branch persists `boxes`/`anchor` correctly, which happens before any OCR:

```bash
python3 - <<'EOF'
import sys, tempfile, os, configparser
sys.path.insert(0, '.')
from cmr_renamer import watcher
from PIL import Image

watcher.pytesseract.image_to_string = lambda *a, **k: ''

def fake_calibra_box(pdf_paths, initial_path, boxes, dpi):
    return {'boxes': [(10, 10, 100, 40), (10, 50, 100, 80)], 'anchor': (30, 60)}

watcher._calibra_box = fake_calibra_box

with tempfile.TemporaryDirectory() as config_dir:
    config_path = os.path.join(config_dir, 'config.ini')
    with open(config_path, 'w') as f:
        f.write("[OCR]\nlang = eng\ndpi = 150\n")
    watcher._get_config_dir = lambda: config_dir

    with tempfile.TemporaryDirectory() as watch_dir:
        target = os.path.join(watch_dir, 'DOC0001.pdf')
        Image.new('RGB', (300, 200), 'white').save(target)

        ocr_cfg = {'boxes': [], 'show_rects': False, 'lang': 'eng', 'dpi': 150, 'anchor': None}
        name_cfg = {'max_length': 50, 'remove_leading_zeros': False}

        watcher._rinomina_pdf(target, ocr_cfg, name_cfg)

    assert ocr_cfg['anchor'] == (30, 60), ocr_cfg
    assert ocr_cfg['boxes'] == [(10, 10, 100, 40), (10, 50, 100, 80)], ocr_cfg

    saved = configparser.ConfigParser()
    saved.read(config_path)
    assert saved['OCR']['anchor_x'] == '30', dict(saved['OCR'])
    assert saved['OCR']['anchor_y'] == '60', dict(saved['OCR'])
    print('OK', ocr_cfg)
EOF
```

Expected output: a `✅ Nuove coordinate salvate → ...` line, a `✅ Rinominato → ...` line (the
stub image OCRs to an empty string via the mocked `pytesseract`, so the file lands as
`documento_senza_nome.pdf` — harmless, not checked by the assertions), then
`OK {'boxes': [(10, 10, 100, 40), (10, 50, 100, 80)], 'show_rects': False, 'lang': 'eng', 'dpi': 150, 'anchor': (30, 60)}`.

- [ ] **Step 4: Verify the tray `_recalibra` wiring**

```bash
python3 - <<'EOF'
import sys, threading, tempfile, os, configparser
sys.path.insert(0, '.')
from cmr_renamer import watcher
from PIL import Image

if not watcher.PYSTRAY_AVAILABLE:
    print("SKIP: pystray not installed in this environment")
    sys.exit(0)

def fake_calibra_box(pdf_paths, initial_path, boxes, dpi):
    return {'boxes': [(1, 2, 3, 4)], 'anchor': (7, 8)}

watcher._calibra_box = fake_calibra_box

with tempfile.TemporaryDirectory() as config_dir:
    config_path = os.path.join(config_dir, 'config.ini')
    with open(config_path, 'w') as f:
        f.write("[OCR]\nlang = eng\ndpi = 150\n")
    watcher._get_config_dir = lambda: config_dir

    icon_img = Image.new('RGB', (16, 16), 'white')
    ocr_cfg = {'boxes': [], 'dpi': 150, 'anchor': None}

    with tempfile.TemporaryDirectory() as watch_dir:
        Image.new('RGB', (300, 200), 'white').save(os.path.join(watch_dir, 'a.pdf'))
        tray = watcher._build_tray_icon(icon_img, ocr_cfg, '/tmp/log.txt', watch_dir, threading.Event())
        item = next(i for i in tray.menu.items if str(i.text) == 'Ricalibra box')
        item(tray)

    assert ocr_cfg['anchor'] == (7, 8), ocr_cfg
    saved = configparser.ConfigParser()
    saved.read(config_path)
    assert saved['OCR']['anchor_x'] == '7', dict(saved['OCR'])
    print('OK', ocr_cfg)
EOF
```

Expected output: `OK {'boxes': [(1, 2, 3, 4)], ...}` (or `SKIP: ...` if `pystray` genuinely isn't
available in whatever environment runs this — treat `SKIP` as not verified, not as a pass).

- [ ] **Step 5: Full-file syntax check**

```bash
python3 -m py_compile cmr_renamer/watcher.py
```

Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Wire anchor persistence into both calibration call sites"
```

---

### Task 5: `_resolve_crop_boxes` — apply drift correction during processing

**Files:**
- Modify: `cmr_renamer/watcher.py` (insert before `_rinomina_pdf`; modify its crop loop)

**Interfaces:**
- Consumes: `_detect_content_anchor(img)` (Task 1), `ocr_cfg['boxes']`, `ocr_cfg['anchor']`,
  `ocr_cfg['dpi']`.
- Produces: `_resolve_crop_boxes(img: Image.Image, ocr_cfg: dict) -> list` — the box list to crop,
  possibly shifted; never raises, always returns a usable list.

- [ ] **Step 1: Add the constant and function**

Insert immediately before `def _rinomina_pdf(...)`:

```python
MAX_ANCHOR_SHIFT_MM = 15  # oltre questa soglia lo spostamento rilevato è considerato inaffidabile


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

- [ ] **Step 2: Wire it into the crop loop**

Replace:

```python
        parti = []
        for box in ocr_cfg['boxes']:
```

with:

```python
        parti = []
        for box in _resolve_crop_boxes(img, ocr_cfg):
```

- [ ] **Step 3: Verify**

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
from cmr_renamer.watcher import _resolve_crop_boxes, _detect_content_anchor

boxes = [(10, 10, 100, 40)]

# 1. Nessuna ancora di riferimento -> nessuna modifica
ocr_cfg = {'boxes': boxes, 'anchor': None, 'dpi': 150}
img = Image.new('RGB', (400, 500), (255, 255, 255))
assert _resolve_crop_boxes(img, ocr_cfg) == boxes
print('OK no reference anchor')

# 2. Spostamento plausibile -> box traslati esattamente del delta rilevato
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

# 3. Pagina corrente quasi bianca -> fallback senza correzione
blank = Image.new('RGB', (400, 500), (255, 255, 255))
ocr_cfg3 = {'boxes': boxes, 'anchor': (10, 20), 'dpi': 150}
assert _resolve_crop_boxes(blank, ocr_cfg3) == boxes
print('OK blank current page fallback')

# 4. Spostamento implausibile -> fallback senza correzione
img4 = Image.new('RGB', (2000, 2500), (255, 255, 255))
draw4 = ImageDraw.Draw(img4)
draw4.rectangle([1000, 1200, 1980, 1210], fill=(0, 0, 0))
draw4.rectangle([1000, 1200, 1010, 2480], fill=(0, 0, 0))
ocr_cfg4 = {'boxes': boxes, 'anchor': (10, 20), 'dpi': 150}
assert _resolve_crop_boxes(img4, ocr_cfg4) == boxes
print('OK implausible shift fallback')
EOF
python3 -m py_compile cmr_renamer/watcher.py
```

Expected output: four `OK ...` lines, then no output from `py_compile` (exit 0).

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add _resolve_crop_boxes, apply drift correction before OCR cropping"
```

---

### Task 6: Load the anchor from config in `run()`

**Files:**
- Modify: `cmr_renamer/watcher.py` (`run()`'s `ocr_cfg` construction)

**Interfaces:**
- Consumes: `_load_anchor_from_config(cfg['OCR'])` (Task 2).
- Produces: `ocr_cfg['anchor']` is now populated from `config.ini` on every startup, so
  `_resolve_crop_boxes` (Task 5) actually activates for installs with a saved anchor.

- [ ] **Step 1: Add `anchor` to `ocr_cfg`**

Replace:

```python
    ocr_cfg = {
        'boxes': _load_boxes_from_config(cfg['OCR']),
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
    }
```

with:

```python
    ocr_cfg = {
        'boxes': _load_boxes_from_config(cfg['OCR']),
        'anchor': _load_anchor_from_config(cfg['OCR']),
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
    }
```

- [ ] **Step 2: Verify**

This exercises the real `run()` config-parsing logic in isolation by constructing the same
`ConfigParser` shape `run()` reads from, without needing to run the whole watcher:

```bash
python3 - <<'EOF'
import sys, configparser
sys.path.insert(0, '.')
from cmr_renamer.watcher import _load_boxes_from_config, _load_anchor_from_config

cfg = configparser.ConfigParser()
cfg.read_string("""
[OCR]
box1 = 1,2,3,4
lang = eng
dpi = 300
anchor_x = 15
anchor_y = 25
""")

ocr_cfg = {
    'boxes': _load_boxes_from_config(cfg['OCR']),
    'anchor': _load_anchor_from_config(cfg['OCR']),
    'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
    'lang': cfg['OCR']['lang'],
    'dpi': int(cfg['OCR']['dpi']),
}
assert ocr_cfg['anchor'] == (15, 25), ocr_cfg
print('OK', ocr_cfg)

# Config senza ancora (pattern retrocompatibile) -> None, nessun errore
cfg2 = configparser.ConfigParser()
cfg2.read_string("[OCR]\nlang = eng\ndpi = 300\n")
assert _load_anchor_from_config(cfg2['OCR']) is None
print('OK missing anchor keys -> None')
EOF
python3 -m py_compile cmr_renamer/watcher.py
```

Expected output: two `OK ...` lines, then no output from `py_compile`.

- [ ] **Step 3: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Load saved content anchor into ocr_cfg on startup"
```

---

### Task 7: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Document the anchor/drift-correction behavior**

Find the paragraph in the "Processing pipeline" section that currently reads (after the multi-file
calibrator sidebar was documented):

```markdown
Colored, numbered selector buttons (one per box, colors match the drawn rectangles) pick which box
the next drag updates, plus `+ Box`/`− Box` buttons (disabled at 5/2 respectively) to change the box
count; saving persists the new box list to `config.ini` via `_save_boxes_to_config` and applies it
immediately to `ocr_cfg` for the file being processed.
```

Replace it with:

```markdown
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
```

- [ ] **Step 2: Verify**

```bash
grep -n "_save_boxes_to_config\|_detect_content_anchor\|_resolve_crop_boxes" CLAUDE.md
```

Expected: no `_save_boxes_to_config` match (renamed), and both `_detect_content_anchor` and
`_resolve_crop_boxes` appear.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document scan drift correction via content anchor in CLAUDE.md"
```

---

### Task 8: Manual verification (human, on a machine with `tkinter`, `poppler`, `tesseract`)

**Files:** none — this is a checklist, not a code change.

Everything except the one line inside `_calibra_box`'s `on_save` (Task 3) has been verified with
real, runnable Python in this sandbox. Before considering this feature done, run these manually
from source (`python main.py`):

- [ ] Calibrate boxes against one real scan of your CMR document. Confirm calibration completes
      normally (no crash) — this exercises the `on_save` → `_detect_content_anchor` path that
      couldn't run in the sandbox.
- [ ] Open `config.ini` and confirm `[OCR]` now has `anchor_x`/`anchor_y` with plausible pixel
      values (roughly where the table's top-left border is on that page, given the configured
      `dpi`).
- [ ] Scan or place the same document shifted a few millimeters vertically and/or horizontally
      (or use a second real scan of the same form) and process it — confirm the renamed output is
      correct (boxes followed the content) even though the raw scan position moved.
- [ ] Process a deliberately blank/near-blank page and confirm it's still renamed (falls back to
      uncorrected boxes) with a warning printed to the log, not a crash.
- [ ] With an existing pre-feature `config.ini` (no `anchor_x`/`anchor_y` — e.g. one saved by an
      older build, or delete those two keys by hand), confirm processing works exactly as before,
      with no drift-correction warnings at all.

- [ ] **Report results back** (pass/fail per bullet) before merging/shipping this change.
