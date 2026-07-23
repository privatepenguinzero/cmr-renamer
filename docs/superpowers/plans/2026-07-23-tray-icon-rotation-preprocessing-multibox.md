# Tray Icon, Log Rotation, OCR Preprocessing, Multi-Box OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the frozen background exe a visible tray presence with controls, cap its log file size, improve OCR accuracy with basic image preprocessing, and let the OCR box count grow from a fixed 2 to a user-configurable 2-5.

**Architecture:** All changes land in `cmr_renamer/watcher.py` (the whole application lives there per project convention), plus a new `assets/icon.ico`, one new dependency (`pystray`), and updates to the PyInstaller build command (local docs + CI workflow). The box1/box2 fixed pair generalizes to an ordered `list[tuple]` stored as `config.ini` keys `box1`..`box5`, which keeps existing 2-box configs loading unchanged.

**Tech Stack:** Python 3.10+, Pillow (already a dependency, adds `ImageOps`), `pystray` (new dependency), `tkinter` (already used, stdlib), `configparser` (stdlib).

## Global Constraints

- No test suite, linter, or formatter exists in this repo (per `CLAUDE.md`) — do not add `pytest` or similar. Verification uses standalone `python3 -c` / temp scripts with `assert` for pure logic, and manual steps for anything requiring `tkinter`/a display/`pystray` (this sandbox has neither `tkinter` nor a display — confirmed via `python3 -c "import tkinter"` failing and `$DISPLAY` being empty — so those steps are manual, to be run by a human on a machine with a display, e.g. Windows).
- OCR box count is configurable from 2 to 5 (`MIN_BOXES = 2`, `MAX_BOXES = 5`).
- Existing `config.ini` files with only `box1`/`box2` must keep working with zero migration step.
- OCR preprocessing (grayscale → autocontrast → threshold at 128) is always on, no config toggle.
- Log rotation caps `cmr-renamer.log` at 1,000,000 bytes with exactly one backup (`cmr-renamer.log.1`).
- Tray icon is active only when `_is_frozen()` is true and background mode is reached; running from source is unchanged.
- All new user-facing strings and helper names follow the existing convention: Italian for console/log strings and internal helper names, English for docstrings/comments.
- `pystray` and the icon asset must be import/load-guarded so `cmr_renamer.watcher` still imports cleanly when `pystray` isn't installed (same pattern as the existing `TKINTER_AVAILABLE` guard).

---

### Task 1: Generate placeholder icon asset

**Files:**
- Create: `assets/icon.ico`

**Interfaces:**
- Produces: `assets/icon.ico`, a multi-resolution (16/32/48/256px) icon file consumed by Task 2 (PyInstaller `--icon`/`--add-data`) and Task 8 (tray icon image).

- [ ] **Step 1: Write a one-off generator script**

Create `/tmp/claude-1000/-var-home-icenoir-coding-test-cmr-renamer/8c14f777-7336-4afd-aeb0-ba449719b2b6/scratchpad/gen_icon.py`:

```python
from PIL import Image, ImageDraw
import os

os.makedirs("assets", exist_ok=True)

img = Image.new("RGBA", (256, 256), (255, 255, 255, 0))
draw = ImageDraw.Draw(img)
draw.rounded_rectangle(
    [28, 16, 228, 240], radius=20,
    fill=(43, 108, 176, 255), outline=(20, 60, 100, 255), width=6,
)
draw.rectangle([60, 64, 196, 80], fill=(255, 255, 255, 255))
draw.rectangle([60, 100, 196, 116], fill=(255, 255, 255, 255))
draw.rectangle([60, 136, 160, 152], fill=(255, 255, 255, 255))
img.save("assets/icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
print("wrote assets/icon.ico")
```

- [ ] **Step 2: Run it from the repo root**

Run: `python3 /tmp/claude-1000/-var-home-icenoir-coding-test-cmr-renamer/8c14f777-7336-4afd-aeb0-ba449719b2b6/scratchpad/gen_icon.py`
Expected: prints `wrote assets/icon.ico`, and `assets/icon.ico` now exists.

- [ ] **Step 3: Verify multi-resolution icon**

Run:
```bash
python3 -c "
from PIL import Image
im = Image.open('assets/icon.ico')
sizes = sorted(s[0] for s in im.info.get('sizes', []))
assert sizes == [16, 32, 48, 256], sizes
print('OK', sizes)
"
```
Expected: `OK [16, 32, 48, 256]`

- [ ] **Step 4: Commit**

```bash
git add assets/icon.ico
git commit -m "Add placeholder application icon"
```

---

### Task 2: Wire icon into PyInstaller build (CI workflow + local docs)

**Files:**
- Modify: `.github/workflows/build_release.yml` (build step, currently around line 44-48)
- Modify: `CLAUDE.md` (Commands section, local build command)

**Interfaces:**
- Consumes: `assets/icon.ico` from Task 1.
- Produces: PyInstaller command now includes `--icon=assets/icon.ico` (embeds the exe's OS-level icon) and `--add-data "assets/icon.ico;assets"` (bundles the file so Task 8's runtime tray-icon loading can find it via `sys._MEIPASS`).

- [ ] **Step 1: Update the CI build step**

In `.github/workflows/build_release.yml`, find:
```yaml
      - name: Build executable
        run: |
          uv run -- pyinstaller --onefile --windowed --name cmr-renamer main.py
        shell: pwsh
```
Replace with:
```yaml
      - name: Build executable
        run: |
          uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" main.py
        shell: pwsh
```

- [ ] **Step 2: Update the local build command in CLAUDE.md**

In `CLAUDE.md`, find:
```
uv run -- pyinstaller --onefile --windowed --name cmr-renamer main.py
```
Replace with:
```
uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" main.py
```

- [ ] **Step 3: Verify both files were updated consistently**

Run: `grep -rn "icon=assets/icon.ico" CLAUDE.md .github/workflows/build_release.yml`
Expected: two matches, one per file, both including `--add-data "assets/icon.ico;assets"` on the same line as `--icon=assets/icon.ico`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build_release.yml CLAUDE.md
git commit -m "Wire application icon into PyInstaller build"
```

---

### Task 3: Rotating log writer

**Files:**
- Modify: `cmr_renamer/watcher.py` — replace `_setup_file_logging` (currently lines 93-98)

**Interfaces:**
- Produces: `_RotatingWriter` class (constructor `_RotatingWriter(path: str, max_bytes: int = 1_000_000)`, with `write(data: str) -> int`, `flush()`, `close()` methods) and an updated `_setup_file_logging(log_dir: str)` that uses it.

- [ ] **Step 1: Replace `_setup_file_logging` with the rotating writer**

In `cmr_renamer/watcher.py`, find:
```python
def _setup_file_logging(log_dir: str):
    """Redirect stdout/stderr to a log file in background mode."""
    log_path = os.path.join(log_dir, 'cmr-renamer.log')
    # Use 'a' for append mode, ensure UTF-8 encoding, replace errors
    sys.stdout = open(log_path, 'a', encoding='utf-8', errors='replace')
    sys.stderr = sys.stdout  # Redirect stderr to the same log file
```
Replace with:
```python
class _RotatingWriter:
    """File-like object that rotates cmr-renamer.log once it exceeds max_bytes.

    Keeps exactly one backup (`<path>.1`) instead of growing unbounded, without
    pulling in the `logging` module (this codebase uses plain `print()` with
    Italian/emoji strings everywhere; migrating every call site to `logging`
    would be an unrelated, large refactor).
    """

    def __init__(self, path: str, max_bytes: int = 1_000_000):
        self.path = path
        self.max_bytes = max_bytes
        self._file = open(path, 'a', encoding='utf-8', errors='replace')

    def write(self, data: str) -> int:
        n = self._file.write(data)
        self._file.flush()
        self._maybe_rotate()
        return n

    def flush(self):
        self._file.flush()

    def close(self):
        self._file.close()

    def _maybe_rotate(self):
        try:
            if os.path.getsize(self.path) < self.max_bytes:
                return
            self._file.close()
            backup = self.path + '.1'
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(self.path, backup)
            self._file = open(self.path, 'a', encoding='utf-8', errors='replace')
        except OSError:
            # Rotation failed (e.g. permission error) — keep appending to the
            # existing file rather than losing log output entirely.
            if self._file.closed:
                self._file = open(self.path, 'a', encoding='utf-8', errors='replace')


def _setup_file_logging(log_dir: str):
    """Redirect stdout/stderr to a size-capped, rotating log file in background mode."""
    log_path = os.path.join(log_dir, 'cmr-renamer.log')
    writer = _RotatingWriter(log_path)
    sys.stdout = writer
    sys.stderr = writer
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `python3 -c "import cmr_renamer.watcher; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify rotation behavior with a standalone script**

Run:
```bash
python3 -c "
import os
from cmr_renamer.watcher import _RotatingWriter

path = '/tmp/claude-1000/-var-home-icenoir-coding-test-cmr-renamer/8c14f777-7336-4afd-aeb0-ba449719b2b6/scratchpad/rotest.log'
backup = path + '.1'
for p in (path, backup):
    if os.path.exists(p):
        os.remove(p)

w = _RotatingWriter(path, max_bytes=50)
for _ in range(10):
    w.write('x' * 10 + chr(10))

assert os.path.exists(backup), 'backup should exist after rotation'
assert os.path.getsize(path) < 50, os.path.getsize(path)
print('OK')
"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add rotating log writer to cap background-mode log file size"
```

---

### Task 4: OCR preprocessing helper

**Files:**
- Modify: `cmr_renamer/watcher.py` — import line (currently line 19: `from PIL import Image`), add new function near `_pulisci_nome` (currently lines 105-112)

**Interfaces:**
- Produces: `_preprocess_for_ocr(img: "Image.Image") -> "Image.Image"`, consumed by Task 7's `_rinomina_pdf` loop.

- [ ] **Step 1: Add `ImageOps` to the PIL import**

Find:
```python
from PIL import Image
```
Replace with:
```python
from PIL import Image, ImageOps
```

- [ ] **Step 2: Add the preprocessing helper**

Directly after `_pulisci_nome`'s closing `return clean` (end of the function currently at lines 105-112), add:

```python
def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Migliora un crop prima dell'OCR: scala di grigi, contrasto, binarizzazione.

    Soglia fissa (128), non derivata per immagine — punto di partenza pensato
    per tuning manuale (vedi verifica del piano), non un default definitivo.
    """
    gray = img.convert('L')
    contrasted = ImageOps.autocontrast(gray)
    return contrasted.point(lambda p: 255 if p > 128 else 0)
```

- [ ] **Step 3: Verify with a standalone script**

Run:
```bash
python3 -c "
from PIL import Image
from cmr_renamer.watcher import _preprocess_for_ocr

img = Image.new('RGB', (10, 10))
for x in range(10):
    for y in range(10):
        v = (x + y) * 12
        img.putpixel((x, y), (v, v, v))

out = _preprocess_for_ocr(img)
assert out.mode == 'L', out.mode
vals = set(out.getdata())
assert vals <= {0, 255}, vals
print('OK', vals)
"
```
Expected: `OK {0, 255}` (or `OK {0}` / `OK {255}` depending on the gradient — either is fine, the assertion is what matters).

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add PIL-only OCR preprocessing (grayscale, autocontrast, threshold)"
```

---

### Task 5: Box config load/save generalization

**Files:**
- Modify: `cmr_renamer/watcher.py` — replace `_save_boxes_to_config` (currently lines 129-139), add `_load_boxes_from_config`

**Interfaces:**
- Produces: `MIN_BOXES = 2`, `MAX_BOXES = 5` module constants; `_save_boxes_to_config(boxes: list[tuple]) -> None`; `_load_boxes_from_config(ocr_section) -> list[tuple]` (takes a `configparser` section object, e.g. `cfg['OCR']`). Consumed by Task 6 (`_calibra_box`), Task 7 (`_rinomina_pdf` and `run()`), Task 8 (tray's recalibrate action).

- [ ] **Step 1: Replace `_save_boxes_to_config` and add `_load_boxes_from_config`**

Find:
```python
def _save_boxes_to_config(box1: tuple, box2: tuple) -> None:
    """Salva le nuove coordinate box1/box2 nel config.ini esistente."""
    config_path = os.path.join(_get_config_dir(), 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path)
    if 'OCR' not in config:
        config['OCR'] = {}
    config['OCR']['box1'] = ','.join(str(int(v)) for v in box1)
    config['OCR']['box2'] = ','.join(str(int(v)) for v in box2)
    with open(config_path, 'w') as f:
        config.write(f)
```
Replace with:
```python
MIN_BOXES = 2
MAX_BOXES = 5


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


def _load_boxes_from_config(ocr_section) -> list:
    """Legge box1..box5 da una sezione [OCR] già caricata, in ordine.

    Si ferma al primo box assente — sufficiente perché _save_boxes_to_config
    scrive sempre chiavi contigue a partire da box1.
    """
    boxes = []
    for i in range(1, MAX_BOXES + 1):
        raw = ocr_section.get(f'box{i}')
        if not raw:
            break
        boxes.append(tuple(map(int, raw.split(','))))
    return boxes
```

- [ ] **Step 2: Verify `_load_boxes_from_config` with a standalone script**

Run:
```bash
python3 -c "
import configparser
from cmr_renamer.watcher import _load_boxes_from_config

cfg = configparser.ConfigParser()
cfg['OCR'] = {'box1': '1,2,3,4', 'box2': '5,6,7,8'}
boxes = _load_boxes_from_config(cfg['OCR'])
assert boxes == [(1, 2, 3, 4), (5, 6, 7, 8)], boxes

cfg2 = configparser.ConfigParser()
cfg2['OCR'] = {}
assert _load_boxes_from_config(cfg2['OCR']) == []

cfg3 = configparser.ConfigParser()
cfg3['OCR'] = {f'box{i}': '0,0,1,1' for i in range(1, 6)}
assert len(_load_boxes_from_config(cfg3['OCR'])) == 5

print('OK')
"
```
Expected: `OK`

- [ ] **Step 3: Verify `_save_boxes_to_config` round-trip and stale-key cleanup**

Run:
```bash
python3 -c "
import os, configparser
from cmr_renamer import watcher

tmpdir = '/tmp/claude-1000/-var-home-icenoir-coding-test-cmr-renamer/8c14f777-7336-4afd-aeb0-ba449719b2b6/scratchpad/cfgtest'
os.makedirs(tmpdir, exist_ok=True)
config_path = os.path.join(tmpdir, 'config.ini')
if os.path.exists(config_path):
    os.remove(config_path)
watcher._get_config_dir = lambda: tmpdir  # monkeypatch for this script only

watcher._save_boxes_to_config([(0, 0, 1, 1), (0, 0, 2, 2), (0, 0, 3, 3), (0, 0, 4, 4)])
cfg = configparser.ConfigParser()
cfg.read(config_path)
assert cfg['OCR']['box4'] == '0,0,4,4'

watcher._save_boxes_to_config([(0, 0, 1, 1), (0, 0, 2, 2)])
cfg = configparser.ConfigParser()
cfg.read(config_path)
assert 'box3' not in cfg['OCR']
assert 'box4' not in cfg['OCR']
print('OK')
"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Generalize box config storage from fixed box1/box2 to a 2-5 box list"
```

---

### Task 6: Calibrator UI generalization (add/remove boxes)

**Files:**
- Modify: `cmr_renamer/watcher.py` — replace `_calibra_box` (currently lines 142-344)

**Interfaces:**
- Consumes: `MIN_BOXES`, `MAX_BOXES` from Task 5.
- Produces: `BOX_COLORS` list, `_default_box(index: int) -> tuple`, `_box_label(index: int) -> str`, and `_calibra_box(img: "Image.Image", boxes: list) -> list | None` (new signature — was `(img, box1, box2)`). Consumed by Task 7 (`_rinomina_pdf`) and Task 8 (tray recalibrate action).

This is a GUI change — it cannot be exercised in this sandbox (no `tkinter`, no display: confirmed via `python3 -c "import tkinter"` failing and `$DISPLAY` being empty). Verification for this task is manual, to be run by a human on a machine with `tkinter` and a display.

- [ ] **Step 1: Replace `_calibra_box`**

Find the entire existing `_calibra_box` function (from `def _calibra_box(img: "Image.Image", box1: tuple, box2: tuple):` through its closing `return None` in the `except Exception as e:` block) and replace it with:

```python
BOX_COLORS = ["red", "blue", "green", "orange", "purple"]


def _default_box(index: int) -> tuple:
    """Rettangolo di default per un nuovo box, offsettato per non sovrapporsi agli altri."""
    offset = 20 * index
    return (20 + offset, 20 + offset, 220 + offset, 120 + offset)


def _box_label(index: int) -> str:
    """Etichetta leggibile per il box all'indice 0-based `index`."""
    semantic = {0: "numero documento", 1: "ragione sociale"}
    if index in semantic:
        return f"box {index + 1} ({semantic[index]})"
    return f"box {index + 1}"


def _calibra_box(img: "Image.Image", boxes: list):
    """Mostra la pagina e permette di ridisegnare 2-5 box col mouse.

    `boxes` è una lista di partenza di 2-5 tuple (x1,y1,x2,y2).
    Ritorna la nuova lista di box se l'utente salva, altrimenti None.
    """
    if not TKINTER_AVAILABLE:
        print("⚠️ tkinter non disponibile: calibrazione box saltata.")
        return None

    MIN_DRAG = 4  # px — ignore accidental clicks/near-zero drags
    MAX_ZOOM = 6.0  # relative to the initial fit-to-screen view
    MAX_DIM = 8000  # px safety cap on the rendered (zoomed) image size

    state = {
        'boxes': list(boxes),
        'active': 0, 'start': None, 'drag_id': None, 'result': None,
        'zoom': 1.0, 'photo': None,
    }
    drawn_ids: dict = {}
    select_buttons: dict = {}

    try:
        root = Tk()
        root.title("CMR Renamer - Calibrazione box OCR")

        screen_w = max(root.winfo_screenwidth() - 150, 300)
        screen_h = max(root.winfo_screenheight() - 260, 300)
        base_scale = min(screen_w / img.width, screen_h / img.height, 1.0)
        viewport_w = max(int(img.width * base_scale), 1)
        viewport_h = max(int(img.height * base_scale), 1)
        max_zoom = min(
            MAX_ZOOM,
            MAX_DIM / (img.width * base_scale),
            MAX_DIM / (img.height * base_scale),
        )

        top_frame = Frame(root)
        top_frame.pack(pady=4)

        select_frame = Frame(top_frame)
        select_frame.pack(side="left")

        count_frame = Frame(top_frame)
        count_frame.pack(side="left", padx=20)

        zoom_frame = Frame(top_frame)
        zoom_frame.pack(side="left", padx=20)

        label = Label(root, text="")
        label.pack(pady=2)

        canvas_frame = Frame(root)
        canvas_frame.pack()

        canvas = Canvas(canvas_frame, width=viewport_w, height=viewport_h, cursor="cross", bg="#333333")
        vbar = Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        hbar = Scrollbar(root, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        canvas.grid(row=0, column=0)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.pack(fill="x")

        image_item = canvas.create_image(0, 0, anchor="nw")

        def total_scale():
            return base_scale * state['zoom']

        def draw_box(index):
            scale = total_scale()
            x1, y1, x2, y2 = [c * scale for c in state['boxes'][index]]
            if index in drawn_ids:
                canvas.delete(drawn_ids[index])
            drawn_ids[index] = canvas.create_rectangle(
                x1, y1, x2, y2, outline=BOX_COLORS[index], width=3
            )

        def draw_all_boxes():
            for old_id in drawn_ids.values():
                canvas.delete(old_id)
            drawn_ids.clear()
            for i in range(len(state['boxes'])):
                draw_box(i)

        def render():
            scale = total_scale()
            disp_w = max(int(img.width * scale), 1)
            disp_h = max(int(img.height * scale), 1)
            state['photo'] = ImageTk.PhotoImage(img.resize((disp_w, disp_h)))
            canvas.itemconfig(image_item, image=state['photo'])
            canvas.configure(scrollregion=(0, 0, disp_w, disp_h))
            draw_all_boxes()
            zoom_label.config(text=f"{int(state['zoom'] * 100)}%")

        def set_zoom(new_zoom):
            new_zoom = max(1.0, min(new_zoom, max_zoom))
            if new_zoom == state['zoom']:
                return
            state['zoom'] = new_zoom
            render()

        def zoom_in():
            set_zoom(state['zoom'] * 1.25)

        def zoom_out():
            set_zoom(state['zoom'] / 1.25)

        def zoom_reset():
            set_zoom(1.0)

        def on_mousewheel(event):
            # Windows/Mac deliver <MouseWheel> with event.delta; Linux uses
            # separate Button-4/Button-5 events instead (handled below).
            if event.delta > 0:
                zoom_in()
            else:
                zoom_out()

        canvas.bind("<MouseWheel>", on_mousewheel)
        canvas.bind("<Button-4>", lambda e: zoom_in())
        canvas.bind("<Button-5>", lambda e: zoom_out())

        Button(zoom_frame, text="−", command=zoom_out, width=3).pack(side="left")
        zoom_label = Label(zoom_frame, text="100%", width=6)
        zoom_label.pack(side="left", padx=4)
        Button(zoom_frame, text="+", command=zoom_in, width=3).pack(side="left")
        Button(zoom_frame, text="Reset zoom", command=zoom_reset).pack(side="left", padx=8)

        def set_active(index):
            state['active'] = index
            for i, btn in select_buttons.items():
                btn.config(relief=("sunken" if i == index else "raised"))
            label.config(text=f"Box attivo: {_box_label(index)}. Trascina col mouse per ridisegnarlo (rotellina per zoomare).")

        def update_count_buttons():
            add_btn.config(state=("disabled" if len(state['boxes']) >= MAX_BOXES else "normal"))
            remove_btn.config(state=("disabled" if len(state['boxes']) <= MIN_BOXES else "normal"))

        def rebuild_select_buttons():
            for btn in select_buttons.values():
                btn.destroy()
            select_buttons.clear()
            for i in range(len(state['boxes'])):
                btn = Button(
                    select_frame, text=_box_label(i), command=lambda n=i: set_active(n),
                    bg=BOX_COLORS[i], fg="white", activebackground=BOX_COLORS[i], activeforeground="white",
                )
                btn.pack(side="left", padx=5)
                select_buttons[i] = btn
            update_count_buttons()

        def add_box():
            if len(state['boxes']) >= MAX_BOXES:
                return
            state['boxes'].append(_default_box(len(state['boxes'])))
            rebuild_select_buttons()
            set_active(len(state['boxes']) - 1)
            render()

        def remove_box():
            if len(state['boxes']) <= MIN_BOXES:
                return
            removed = state['active']
            del state['boxes'][removed]
            rebuild_select_buttons()
            set_active(max(removed - 1, 0))
            render()

        add_btn = Button(count_frame, text="+ Box", command=add_box)
        add_btn.pack(side="left", padx=2)
        remove_btn = Button(count_frame, text="− Box", command=remove_box)
        remove_btn.pack(side="left", padx=2)

        rebuild_select_buttons()
        set_active(0)
        render()

        def on_press(event):
            state['start'] = (canvas.canvasx(event.x), canvas.canvasy(event.y))

        def on_drag(event):
            if state['start'] is None:
                return
            if state['drag_id'] is not None:
                canvas.delete(state['drag_id'])
            x0, y0 = state['start']
            cx, cy = canvas.canvasx(event.x), canvas.canvasy(event.y)
            state['drag_id'] = canvas.create_rectangle(
                x0, y0, cx, cy, outline=BOX_COLORS[state['active']], width=2, dash=(4, 2)
            )

        def on_release(event):
            if state['drag_id'] is not None:
                canvas.delete(state['drag_id'])
                state['drag_id'] = None
            if state['start'] is None:
                return
            x0, y0 = state['start']
            state['start'] = None
            x1, y1 = canvas.canvasx(event.x), canvas.canvasy(event.y)

            if abs(x1 - x0) < MIN_DRAG or abs(y1 - y0) < MIN_DRAG:
                return  # too small to be an intentional box — ignore

            scale = total_scale()
            disp_w = max(int(img.width * scale), 1)
            disp_h = max(int(img.height * scale), 1)
            x0 = min(max(x0, 0), disp_w)
            x1 = min(max(x1, 0), disp_w)
            y0 = min(max(y0, 0), disp_h)
            y1 = min(max(y1, 0), disp_h)
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))

            index = state['active']
            state['boxes'][index] = (int(x0 / scale), int(y0 / scale), int(x1 / scale), int(y1 / scale))
            draw_box(index)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        btn_frame = Frame(root)
        btn_frame.pack(pady=8)

        def on_save():
            state['result'] = list(state['boxes'])
            root.destroy()

        def on_cancel():
            state['result'] = None
            root.destroy()

        Button(btn_frame, text="Salva", command=on_save).pack(side="left", padx=5)
        Button(btn_frame, text="Annulla", command=on_cancel).pack(side="left", padx=5)

        root.mainloop()
        return state['result']
    except Exception as e:
        print(f"⚠️ Errore durante la calibrazione dei box: {e}")
        return None
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `python3 -c "import cmr_renamer.watcher; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Manual verification (requires tkinter + a display — e.g. Windows)**

On a machine with `tkinter` and a display:
1. Run the app from source against a folder with a sample PDF whose name starts with the configured prefix, with `config.ini`'s `[OCR]` section having no `box1`/`box2` keys (or delete them) so calibration is forced.
2. Confirm the calibrator window opens with 2 default boxes and matching colored selector buttons.
3. Click `+ Box` three times — confirm 5 selector buttons total, `+ Box` becomes disabled, each new box appears as a small default rectangle in a distinct color.
4. Drag each box into place, click `− Box` twice — confirm it removes the *currently selected* box each time, `− Box` disables at 2 boxes remaining.
5. Click "Salva" — confirm `config.ini` now has exactly as many `boxN` keys as boxes left, with the drawn coordinates.
6. Re-run the app — confirm it loads that same box count without re-prompting for calibration.

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Generalize box calibrator to support 2-5 boxes with add/remove"
```

---

### Task 7: Multi-box processing in `_rinomina_pdf` + `run()` box wiring

**Files:**
- Modify: `cmr_renamer/watcher.py` — `_rinomina_pdf` (currently lines 347-394), `run()`'s OCR config parsing (currently lines 494-502)

**Interfaces:**
- Consumes: `_preprocess_for_ocr` (Task 4), `MIN_BOXES`, `_load_boxes_from_config`, `_save_boxes_to_config` (Task 5), `_default_box`, `_calibra_box` (Task 6).
- Produces: `ocr_cfg['boxes']` (a `list[tuple]`, replacing the old `ocr_cfg['box1']`/`ocr_cfg['box2']` keys) — consumed by Task 8's tray recalibrate action.

- [ ] **Step 1: Update `run()`'s OCR config parsing**

Find:
```python
    box1_raw = cfg['OCR'].get('box1')
    box2_raw = cfg['OCR'].get('box2')
    ocr_cfg = {
        'box1': tuple(map(int, box1_raw.split(','))) if box1_raw else None,
        'box2': tuple(map(int, box2_raw.split(','))) if box2_raw else None,
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
    }
```
Replace with:
```python
    ocr_cfg = {
        'boxes': _load_boxes_from_config(cfg['OCR']),
        'show_rects': cfg['OCR'].getboolean('show_rects', fallback=False),
        'lang': cfg['OCR']['lang'],
        'dpi': int(cfg['OCR']['dpi']),
    }
```

Also update the comment directly above this block (currently: `# box1/box2 are absent from freshly-created config.ini files (...)`) to:
```python
    # box1..box5 are absent from freshly-created config.ini files (the box
    # count and coordinates are selected with the mouse on the first PDF
    # processed, not prompted for at setup time); show_rects likewise has no
    # setup prompt and only takes effect if a user hand-edits config.ini.
```

- [ ] **Step 2: Update `_rinomina_pdf`'s calibration + OCR loop**

Find:
```python
        serve_calibrazione = ocr_cfg['box1'] is None or ocr_cfg['box2'] is None
        if ocr_cfg['show_rects'] or serve_calibrazione:
            if serve_calibrazione:
                print("🖱️ Box OCR non ancora configurati: selezionali con il mouse.")
            nuovi_box = _calibra_box(
                img, ocr_cfg['box1'] or (0, 0, 0, 0), ocr_cfg['box2'] or (0, 0, 0, 0)
            )
            if nuovi_box:
                ocr_cfg['box1'], ocr_cfg['box2'] = nuovi_box
                _save_boxes_to_config(ocr_cfg['box1'], ocr_cfg['box2'])
                print(f"✅ Nuove coordinate salvate → box1={ocr_cfg['box1']} box2={ocr_cfg['box2']}")
            elif serve_calibrazione:
                print(f"⚠️ Calibrazione annullata: '{os.path.basename(pdf_path)}' non elaborato (nessun box configurato).")
                return

        testo1 = pytesseract.image_to_string(
            img.crop(ocr_cfg['box1']), lang=ocr_cfg['lang']
        )
        testo2 = pytesseract.image_to_string(
            img.crop(ocr_cfg['box2']), lang=ocr_cfg['lang']
        )

        base = f"{_pulisci_nome(testo1, name_cfg['max_length'], name_cfg['remove_leading_zeros'])} {_pulisci_nome(testo2, name_cfg['max_length'], name_cfg['remove_leading_zeros'])}".strip()
        if not base:
            base = "documento_senza_nome"
```
Replace with:
```python
        serve_calibrazione = len(ocr_cfg['boxes']) < MIN_BOXES
        if ocr_cfg['show_rects'] or serve_calibrazione:
            if serve_calibrazione:
                print("🖱️ Box OCR non ancora configurati: selezionali con il mouse.")
            boxes_seed = list(ocr_cfg['boxes'])
            while len(boxes_seed) < MIN_BOXES:
                boxes_seed.append(_default_box(len(boxes_seed)))
            nuovi_box = _calibra_box(img, boxes_seed)
            if nuovi_box:
                ocr_cfg['boxes'] = nuovi_box
                _save_boxes_to_config(ocr_cfg['boxes'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
            elif serve_calibrazione:
                print(f"⚠️ Calibrazione annullata: '{os.path.basename(pdf_path)}' non elaborato (nessun box configurato).")
                return

        parti = []
        for box in ocr_cfg['boxes']:
            crop = _preprocess_for_ocr(img.crop(box))
            testo = pytesseract.image_to_string(crop, lang=ocr_cfg['lang'])
            pulito = _pulisci_nome(testo, name_cfg['max_length'], name_cfg['remove_leading_zeros'])
            if pulito:
                parti.append(pulito)

        base = " ".join(parti).strip()
        if not base:
            base = "documento_senza_nome"
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `python3 -c "import cmr_renamer.watcher; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Verify the non-empty-join fix with a standalone script**

This exercises the string-joining logic in isolation (the same logic now used inline in `_rinomina_pdf`), confirming an empty middle box no longer produces a double space:
```bash
python3 -c "
parti = ['ABC123', '', 'Acme Srl']
base = ' '.join(p for p in parti if p).strip()
assert base == 'ABC123 Acme Srl', repr(base)
print('OK')
"
```
Expected: `OK`

- [ ] **Step 5: Manual verification (requires a real PDF, Tesseract installed)**

Process a sample PDF with an existing 2-box `config.ini` (from before this change, or created by Task 6's manual test) and confirm:
- It still renames using both boxes, in order, with a single space between them and no leading/trailing whitespace.
- OCR output looks at least as accurate as before (preprocessing is now applied) — no automated accuracy metric, subjective before/after check per the spec's Testing section.

- [ ] **Step 6: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Loop OCR over N configured boxes with preprocessing and non-empty join"
```

---

### Task 8: System tray integration

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Modify: `cmr_renamer/watcher.py` — imports (currently lines 9-30), `run()` (currently lines 446-547)

**Interfaces:**
- Consumes: `assets/icon.ico` (Task 1/2), `MIN_BOXES`, `_load_boxes_from_config`, `_save_boxes_to_config` (Task 5), `_default_box`, `_calibra_box` (Task 6).
- Produces: `_get_resource_path(relative_path: str) -> str`, `_build_tray_icon(icon_image, ocr_cfg, log_path, cartella, stop_event) -> "pystray.Icon"`. Nothing outside this task consumes these — this is the last integration point.

This task's end-to-end behavior (an actual tray icon, real menu clicks) cannot be exercised in this sandbox (no `tkinter`, no display, `pystray` not installed). Verification here is: (a) automatable import-guard checks, run now, and (b) a manual end-to-end pass on Windows.

- [ ] **Step 1: Add the `pystray` dependency**

In `pyproject.toml`, find:
```toml
dependencies = [
    "watchdog",
    "pillow",
    "pytesseract",
    "pdf2image",
]
```
Replace with:
```toml
dependencies = [
    "watchdog",
    "pillow",
    "pytesseract",
    "pdf2image",
    "pystray",
]
```

- [ ] **Step 2: Add `threading` and guarded `pystray` imports**

Find:
```python
import os
import re
import sys
import time
import ctypes  # For Windows console manipulation
import atexit
import configparser
```
Replace with:
```python
import os
import re
import sys
import time
import ctypes  # For Windows console manipulation
import atexit
import threading
import configparser
```

Find:
```python
try:
    from tkinter import Tk, Canvas, Button, Label, Frame, Scrollbar
    from PIL import ImageTk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
```
Replace with:
```python
try:
    from tkinter import Tk, Canvas, Button, Label, Frame, Scrollbar
    from tkinter.filedialog import askopenfilename
    from PIL import ImageTk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False
```

- [ ] **Step 3: Verify the module still imports cleanly without `pystray` installed**

Run: `python3 -c "import cmr_renamer.watcher as w; assert w.PYSTRAY_AVAILABLE is False; print('OK')"`
Expected: `OK` (confirms the guard degrades the same way `TKINTER_AVAILABLE` already does — `pystray` isn't installed in this sandbox, which is the environment this check is meant to prove works).

- [ ] **Step 4: Add the resource-path helper**

Directly after `_get_config_dir` (currently lines 42-49), add:
```python
def _get_resource_path(relative_path: str) -> str:
    """Risolve un percorso di risorsa bundled, sia da sorgente che da frozen (PyInstaller onefile)."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative_path)
```

- [ ] **Step 5: Verify the resource path resolves correctly from source**

Run (from the repo root):
```bash
python3 -c "
from cmr_renamer.watcher import _get_resource_path
import os
path = _get_resource_path(os.path.join('assets', 'icon.ico'))
assert os.path.exists(path), path
print('OK', path)
"
```
Expected: `OK <repo>/assets/icon.ico`

- [ ] **Step 6: Add `_build_tray_icon`**

Directly after `_calibra_box` (added in Task 6), add:
```python
def _build_tray_icon(icon_image: "Image.Image", ocr_cfg: dict, log_path: str,
                      cartella: str, stop_event: "threading.Event") -> "pystray.Icon":
    """Crea l'icona di system tray con il menu di controllo del background mode."""

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

    def _recalibra(icon, item):
        root = Tk()
        root.withdraw()
        pdf_path = askopenfilename(title="Seleziona un PDF da calibrare", filetypes=[("PDF", "*.pdf")])
        root.destroy()
        if not pdf_path:
            return
        try:
            immagini = convert_from_path(pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1)
            boxes_seed = list(ocr_cfg['boxes'])
            while len(boxes_seed) < MIN_BOXES:
                boxes_seed.append(_default_box(len(boxes_seed)))
            nuovi_box = _calibra_box(immagini[0], boxes_seed)
            if nuovi_box:
                ocr_cfg['boxes'] = nuovi_box
                _save_boxes_to_config(ocr_cfg['boxes'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
        except Exception as e:
            print(f"⚠️ Errore durante la ricalibrazione: {e}")

    def _exit(icon, item):
        icon.stop()
        stop_event.set()

    menu = pystray.Menu(
        pystray.MenuItem("Apri log", _open_log),
        pystray.MenuItem("Apri cartella monitorata", _open_folder),
        pystray.MenuItem("Ricalibra box", _recalibra),
        pystray.MenuItem("Esci", _exit),
    )
    return pystray.Icon("cmr-renamer", icon_image, "CMR Renamer", menu)
```

- [ ] **Step 7: Wire the tray into `run()`'s main loop**

Find:
```python
    handler = CMRHandler(ocr_cfg, name_cfg, delay_riavvio, prefix)
    observer = Observer()
    try:
        observer.schedule(handler, cartella, recursive=False)
        observer.start()
    except FileNotFoundError:
        print(f"❌ Errore: La cartella monitorata '{cartella}' non è valida.")
        return 1 # Indicate error
    except Exception as e:
        print(f"❌ Errore durante l'avvio del watcher: {e}")
        return 1 # Indicate error

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arresto...")
        observer.stop()

    observer.join()
    print("👋 Monitoraggio terminato.")
    return 0
```
Replace with:
```python
    handler = CMRHandler(ocr_cfg, name_cfg, delay_riavvio, prefix)
    observer = Observer()
    try:
        observer.schedule(handler, cartella, recursive=False)
        observer.start()
    except FileNotFoundError:
        print(f"❌ Errore: La cartella monitorata '{cartella}' non è valida.")
        return 1 # Indicate error
    except Exception as e:
        print(f"❌ Errore durante l'avvio del watcher: {e}")
        return 1 # Indicate error

    tray_icon = None
    stop_event = threading.Event()
    if _is_frozen() and PYSTRAY_AVAILABLE:
        try:
            icon_image = Image.open(_get_resource_path(os.path.join('assets', 'icon.ico')))
            log_path = os.path.join(config_dir, 'cmr-renamer.log')
            tray_icon = _build_tray_icon(icon_image, ocr_cfg, log_path, cartella, stop_event)
            threading.Thread(target=tray_icon.run, daemon=True).start()
        except Exception as e:
            print(f"⚠️ Icona di system tray non disponibile: {e}")
            tray_icon = None

    try:
        if tray_icon is not None:
            stop_event.wait()
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Arresto...")

    observer.stop()
    observer.join()
    print("👋 Monitoraggio terminato.")
    return 0
```

- [ ] **Step 8: Verify the module still imports cleanly**

Run: `python3 -c "import cmr_renamer.watcher; print('OK')"`
Expected: `OK`

- [ ] **Step 9: Manual verification (requires Windows, a built exe, `pystray` installed)**

Build the exe (per Task 2's updated command) and run it in a folder with no `config.ini`, complete first-run setup, then confirm:
1. A tray icon appears using `assets/icon.ico`'s image, once background mode is reached.
2. Right-click menu shows: "Apri log", "Apri cartella monitorata", "Ricalibra box", "Esci".
3. "Apri log" opens `cmr-renamer.log` in the default viewer.
4. "Apri cartella monitorata" opens the watched folder in Explorer.
5. "Ricalibra box" opens a file picker, then the calibrator against the chosen PDF's first page; saving updates `config.ini` and takes effect on the next processed file.
6. "Esci" cleanly stops the process (no hung process in Task Manager).
7. Running from source (`python main.py`) shows no tray icon and behaves exactly as before this task.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml cmr_renamer/watcher.py
git commit -m "Add system tray icon with log/folder/recalibrate/exit controls"
```

---

### Task 9: Update CLAUDE.md architecture docs

**Files:**
- Modify: `CLAUDE.md` (Architecture section)

**Interfaces:**
- None — documentation only, no code interfaces.

- [ ] **Step 1: Update the "Config sections" list**

Find:
```
Config sections: `[Watcher]` (folder, prefix, delay_riavvio), `[OCR]` (box1/box2 crop coordinates,
show_rects debug flag, lang, dpi), `[Filename]` (max_length, remove_leading_zeros).
```
Replace with:
```
Config sections: `[Watcher]` (folder, prefix, delay_riavvio), `[OCR]` (box1..box5 crop coordinates
for 2-5 boxes, show_rects debug flag, lang, dpi), `[Filename]` (max_length, remove_leading_zeros).
```

- [ ] **Step 2: Update the "Config lives next to the executable" paragraph**

Find the sentence:
```
`box1`/`box2`/`show_rects` follow the same optional-key pattern deliberately: `config.py` never
prompts for them (no generic crop coordinates make sense across documents), so `ocr_cfg['box1']`/
`box2'` are `None` until the mouse calibrator (see below) fills them in and writes them back with
`_save_boxes_to_config`; `show_rects` has no setup prompt at all and only takes effect if a user hand-edits
`config.ini` to add it.
```
Replace with:
```
`box1`..`box5`/`show_rects` follow the same optional-key pattern deliberately: `config.py` never
prompts for them (no generic crop coordinates make sense across documents), so `ocr_cfg['boxes']`
is an empty list until the mouse calibrator (see below) fills it in and writes it back with
`_save_boxes_to_config`; `show_rects` has no setup prompt at all and only takes effect if a user hand-edits
`config.ini` to add it. The box count is configurable from 2 to 5 (`MIN_BOXES`/`MAX_BOXES` in
`watcher.py`) via `+`/`−` buttons in the calibrator itself, not a config prompt; existing
`config.ini` files with only `box1`/`box2` load transparently as a 2-box config.
```

- [ ] **Step 3: Update the "Processing pipeline" paragraph**

Find:
```
**Processing pipeline** (`_rinomina_pdf`): `pdf2image.convert_from_path` renders page 1 → `PIL` crops
the two configured boxes → `pytesseract.image_to_string` OCRs each crop → `_pulisci_nome` strips
non-word characters, truncates to `max_length`, optionally strips leading zeros → the two cleaned
strings are joined into the new filename, with `(1)`, `(2)`, ... appended on collision. Before OCR,
`_rinomina_pdf` calibrates the crop boxes via `_calibra_box` whenever `box1`/`box2` are still `None`
(first PDF ever processed — the calibrator is mandatory then, and cancelling skips that file rather
than cropping garbage) or whenever `show_rects` is `True` in `config.ini` (opt-in recalibration).
`_calibra_box` opens a Tk window with the rendered page on a scrollable/zoomable `Canvas` (mouse wheel
or +/− buttons, scaled around a `base_scale` fit-to-screen and clamped by `MAX_ZOOM`/`MAX_DIM`) with
colored "Box 1"/"Box 2" selector buttons (colors match the drawn rectangles) picking which box the
next drag updates; saving persists the new coordinates to `config.ini` via `_save_boxes_to_config` and
applies them immediately to `ocr_cfg` for the file being processed.
```
Replace with:
```
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
```

- [ ] **Step 4: Add a paragraph on the tray icon and log rotation**

Directly after the "Watching" paragraph (the one describing `CMRHandler`), add a new paragraph:
```
**Background mode UI**: when frozen and running in background mode, a system tray icon
(`pystray`, guarded the same way as the `tkinter` import — missing `pystray` just means no tray,
not a crash) offers "Apri log", "Apri cartella monitorata", "Ricalibra box" (opens a file picker
and runs the same `_calibra_box` calibrator against a chosen PDF), and "Esci". This is the only way
to exit a frozen+windowed instance, since it has no console/Ctrl+C available. Log output in
background mode goes through `_RotatingWriter`, which caps `cmr-renamer.log` at ~1MB with one
backup (`cmr-renamer.log.1`) instead of growing unbounded.
```

- [ ] **Step 5: Verify the doc reads coherently**

Run: `grep -n "box1/box2\|ocr_cfg\['box" CLAUDE.md`
Expected: no output (confirms no stale `box1`/`box2`-specific references remain — the intentional
backward-compatibility mention in Step 2's replacement text says "box1`/`box2`-only" instead, which
this pattern does not match).

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md architecture docs for tray icon, log rotation, multi-box OCR"
```
