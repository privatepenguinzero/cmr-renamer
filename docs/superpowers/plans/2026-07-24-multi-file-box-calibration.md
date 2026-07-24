# Multi-file preview in the box calibrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sidebar to the box calibrator listing every PDF in the watched folder, so box positions can be checked live against multiple real documents instead of just the one file that triggered calibration.

**Architecture:** `_calibra_box` in `cmr_renamer/watcher.py` changes from taking a single rendered image to taking a list of PDF paths plus an initial selection; it lazily renders and caches each file's page 1 on first selection and redraws the (unchanged) box overlay on top. Both existing calibration entry points — the mandatory first-run calibration inside `_rinomina_pdf`, and the tray's "Ricalibra box" — are rewired to build that file list from the watched folder instead of a single path (and, for the tray, instead of a file-open dialog).

**Tech Stack:** Python, `tkinter` (Listbox/Scrollbar, already-imported widgets), `pdf2image`/`poppler` (already a dependency). No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-24-multi-file-box-calibration-design.md`.
- No automated test suite exists in this repo (per `CLAUDE.md`) — verification below uses ad-hoc
  Python scripts run via `python3`, not `pytest`. Do not add a `pytest` dependency or a `tests/`
  directory as part of this plan.
- This sandbox has `tkinter` and `pystray` unavailable/partially available — see per-task notes.
  Where a step cannot be exercised end-to-end here, the plan says so explicitly and defers to
  manual verification on a machine with `tkinter` installed (e.g. the Windows target machine).
- Box coordinates are never transformed when switching the displayed file — same pixel space for
  every render because `dpi` is one global config value. Do not add any per-file coordinate
  remapping.
- No OCR runs when switching files in the calibrator — this is a visual check only.
- The tray's `askopenfilename` file-picker for "Ricalibra box" is removed entirely, not kept as a
  fallback.
- Follow existing code conventions in `watcher.py`: Italian user-facing strings/log messages,
  English comments only where the *why* isn't obvious, `state` dict pattern for calibrator mutable
  state (avoid introducing bare `nonlocal` where the existing pattern already covers it).

---

### Task 1: `_list_watched_pdfs` helper

**Files:**
- Modify: `cmr_renamer/watcher.py` (insert new function after `_box_label`, before `_calibra_box`)

**Interfaces:**
- Produces: `_list_watched_pdfs(folder: str) -> list[str]` — absolute paths of every `*.pdf` in
  `folder`, sorted alphabetically by filename. Returns `[]` if `folder` doesn't exist. Case-sensitive
  `.pdf` match, consistent with existing `fname.endswith('.pdf')` checks elsewhere in this file.

- [ ] **Step 1: Add the function**

Insert immediately after `_box_label` (the function ending with `return f"box {index + 1}"`) and
before `def _calibra_box(...)`:

```python
def _list_watched_pdfs(folder: str) -> list:
    """Elenca i percorsi assoluti dei PDF nella cartella, ordinati per nome file."""
    if not os.path.isdir(folder):
        return []
    nomi = sorted(f for f in os.listdir(folder) if f.endswith('.pdf'))
    return [os.path.join(folder, f) for f in nomi]
```

- [ ] **Step 2: Verify**

Run:

```bash
python3 - <<'EOF'
import sys, tempfile, os
sys.path.insert(0, '.')
from cmr_renamer.watcher import _list_watched_pdfs

with tempfile.TemporaryDirectory() as d:
    for name in ['b.pdf', 'a.pdf', 'ignore.txt', 'C.PDF']:
        open(os.path.join(d, name), 'w').close()
    result = _list_watched_pdfs(d)
    assert result == [os.path.join(d, 'a.pdf'), os.path.join(d, 'b.pdf')], result
    assert _list_watched_pdfs('/nonexistent/path/xyz') == []
    print('OK')
EOF
```

Expected output: `OK`

- [ ] **Step 3: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add _list_watched_pdfs helper for scanning the watched folder"
```

---

### Task 2: `_render_pdf_page` helper, rewire `_rinomina_pdf`

**Files:**
- Modify: `cmr_renamer/watcher.py` (insert helper in the "OCR & rename helpers" section; modify
  `_rinomina_pdf`'s render call)

**Interfaces:**
- Consumes: existing `_get_poppler_path()`, `convert_from_path` (already imported).
- Produces: `_render_pdf_page(path: str, dpi: int) -> Image.Image` — renders page 1 of `path` at
  `dpi`, resolving the bundled poppler path when frozen exactly like the code it replaces.

- [ ] **Step 1: Add the helper**

Insert immediately before `def _preprocess_for_ocr(...)`:

```python
def _render_pdf_page(path: str, dpi: int) -> "Image.Image":
    """Renderizza la pagina 1 di un PDF come immagine PIL."""
    immagini = convert_from_path(
        path, dpi=dpi, first_page=1, last_page=1,
        poppler_path=_get_poppler_path(),
    )
    return immagini[0]
```

- [ ] **Step 2: Rewire `_rinomina_pdf`**

In `_rinomina_pdf`, replace:

```python
        immagini = convert_from_path(
            pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1,
            poppler_path=_get_poppler_path(),
        )
        img = immagini[0]
```

with:

```python
        img = _render_pdf_page(pdf_path, ocr_cfg['dpi'])
```

- [ ] **Step 3: Verify**

Run (uses a real Pillow-generated PDF and the real `poppler` binaries already on this machine — no
mocking):

```bash
python3 - <<'EOF'
import sys, tempfile, os
sys.path.insert(0, '.')
from cmr_renamer.watcher import _render_pdf_page
from PIL import Image

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, 'test.pdf')
    Image.new('RGB', (300, 200), 'white').save(path)
    img = _render_pdf_page(path, dpi=150)
    assert img.size[0] > 0 and img.size[1] > 0, img.size
    print('OK', img.size)
EOF
```

Expected output: `OK (<width>, <height>)` with positive dimensions.

- [ ] **Step 4: Full-file syntax check**

```bash
python3 -m py_compile cmr_renamer/watcher.py
```

Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Extract _render_pdf_page helper, reuse it in _rinomina_pdf"
```

---

### Task 3: Multi-file sidebar in `_calibra_box`

**Files:**
- Modify: `cmr_renamer/watcher.py` (tkinter import line; full rewrite of `_calibra_box`)

**Interfaces:**
- Consumes: `_render_pdf_page(path, dpi)` (Task 2), `TKINTER_AVAILABLE`, `BOX_COLORS`, `MIN_BOXES`,
  `MAX_BOXES`, `_default_box`, `_box_label` (all pre-existing, unchanged).
- Produces: `_calibra_box(pdf_paths: list[str], initial_path: str, boxes: list, dpi: int) -> list | None`
  — replaces the old `_calibra_box(img, boxes)` signature. Returns the saved box list (2-5 tuples)
  on save, `None` on cancel or if `tkinter` is unavailable.

**Note on testability:** this sandbox does not have `tkinter` installed (`ModuleNotFoundError` on
`import tkinter`), so the interactive window itself cannot be opened or clicked through here. Steps
2-3 below verify what's mechanically checkable (syntax, signature, the "no tkinter" fallback path)
in this environment. Full interactive verification (sidebar renders, clicking a filename swaps the
image while boxes stay put, drag/add/remove/save still work) must happen on a machine with
`tkinter` available — call this out explicitly when reporting progress; do not claim the GUI was
verified.

- [ ] **Step 1: Add `Listbox` to the tkinter import**

Replace:

```python
    from tkinter import Tk, Canvas, Button, Label, Frame, Scrollbar
```

with:

```python
    from tkinter import Tk, Canvas, Button, Label, Frame, Scrollbar, Listbox
```

- [ ] **Step 2: Replace `_calibra_box` in full**

Replace the entire existing function (from `def _calibra_box(img: "Image.Image", boxes: list):`
through the closing `return None` of its `except Exception as e:` block) with:

```python
def _calibra_box(pdf_paths: list, initial_path: str, boxes: list, dpi: int):
    """Mostra la pagina 1 di un PDF a scelta tra `pdf_paths` e permette di ridisegnare 2-5 box col mouse.

    `pdf_paths` è l'elenco dei PDF della cartella monitorata, selezionabili da una lista laterale
    per confrontare visivamente se i box calibrati si applicano bene a più documenti; il cambio file
    ridisegna solo l'immagine di sfondo, i box restano nelle stesse coordinate. `initial_path` è il
    file mostrato all'apertura (preselezionato in lista). `boxes` è una lista di partenza di 2-5
    tuple (x1,y1,x2,y2). Ritorna la nuova lista di box se l'utente salva, altrimenti None.
    """
    if not TKINTER_AVAILABLE:
        print("⚠️ tkinter non disponibile: calibrazione box saltata.")
        return None

    MIN_DRAG = 4  # px — ignore accidental clicks/near-zero drags
    MAX_ZOOM = 6.0  # relative to the initial fit-to-screen view
    MAX_DIM = 8000  # px safety cap on the rendered (zoomed) image size

    image_cache: dict = {}

    def get_image(path: str) -> "Image.Image":
        if path not in image_cache:
            image_cache[path] = _render_pdf_page(path, dpi)
        return image_cache[path]

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
        root.title("CMR Renamer - Calibrazione box OCR")

        screen_w = max(root.winfo_screenwidth() - 150, 300)
        screen_h = max(root.winfo_screenheight() - 260, 300)
        base_scale = min(screen_w / state['img'].width, screen_h / state['img'].height, 1.0)
        viewport_w = max(int(state['img'].width * base_scale), 1)
        viewport_h = max(int(state['img'].height * base_scale), 1)
        max_zoom = min(
            MAX_ZOOM,
            MAX_DIM / (state['img'].width * base_scale),
            MAX_DIM / (state['img'].height * base_scale),
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

        body_frame = Frame(root)
        body_frame.pack()

        sidebar_frame = Frame(body_frame)
        sidebar_frame.pack(side="left", fill="y", padx=(4, 4))

        Label(sidebar_frame, text="File nella cartella:").pack(anchor="w")
        file_listbox = Listbox(sidebar_frame, width=32, height=28, exportselection=False)
        file_scrollbar = Scrollbar(sidebar_frame, orient="vertical", command=file_listbox.yview)
        file_listbox.configure(yscrollcommand=file_scrollbar.set)
        file_listbox.pack(side="left", fill="y")
        file_scrollbar.pack(side="left", fill="y")

        for path in pdf_paths:
            file_listbox.insert("end", os.path.basename(path))
        if initial_path in pdf_paths:
            initial_index = pdf_paths.index(initial_path)
            file_listbox.selection_set(initial_index)
            file_listbox.see(initial_index)

        canvas_frame = Frame(body_frame)
        canvas_frame.pack(side="left")

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
            disp_w = max(int(state['img'].width * scale), 1)
            disp_h = max(int(state['img'].height * scale), 1)
            state['photo'] = ImageTk.PhotoImage(state['img'].resize((disp_w, disp_h)))
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

        def on_file_select(event=None):
            selection = file_listbox.curselection()
            if not selection:
                return
            path = pdf_paths[selection[0]]
            if path == state['current_path']:
                return
            try:
                new_img = get_image(path)
            except Exception as e:
                print(f"⚠️ Impossibile aprire '{os.path.basename(path)}': {e}")
                file_listbox.selection_clear(0, "end")
                file_listbox.selection_set(pdf_paths.index(state['current_path']))
                return
            state['img'] = new_img
            state['current_path'] = path
            render()

        file_listbox.bind("<<ListboxSelect>>", on_file_select)

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
            disp_w = max(int(state['img'].width * scale), 1)
            disp_h = max(int(state['img'].height * scale), 1)
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

- [ ] **Step 3: Verify what's checkable without `tkinter`**

```bash
python3 -m py_compile cmr_renamer/watcher.py
python3 -c "
import sys
sys.path.insert(0, '.')
from cmr_renamer import watcher
result = watcher._calibra_box(['/tmp/a.pdf', '/tmp/b.pdf'], '/tmp/a.pdf', [(0,0,10,10), (0,0,10,10)], 150)
assert result is None
print('OK: new signature accepted, graceful no-tkinter fallback')
"
```

Expected output: `OK: new signature accepted, graceful no-tkinter fallback` (this only proves the
signature and the `TKINTER_AVAILABLE` guard work — it does not exercise the sidebar UI itself; see
the note above Step 1).

- [ ] **Step 4: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Add multi-file sidebar to the box calibrator"
```

---

### Task 4: Wire the mandatory first-run calibration entry point

**Files:**
- Modify: `cmr_renamer/watcher.py` (`_rinomina_pdf`)

**Interfaces:**
- Consumes: `_list_watched_pdfs` (Task 1), `_calibra_box(pdf_paths, initial_path, boxes, dpi)` (Task 3).

- [ ] **Step 1: Update the calibration call**

In `_rinomina_pdf`, replace:

```python
                    boxes_seed = list(ocr_cfg['boxes'])
                    while len(boxes_seed) < MIN_BOXES:
                        boxes_seed.append(_default_box(len(boxes_seed)))
                    nuovi_box = _calibra_box(img, boxes_seed)
```

with:

```python
                    boxes_seed = list(ocr_cfg['boxes'])
                    while len(boxes_seed) < MIN_BOXES:
                        boxes_seed.append(_default_box(len(boxes_seed)))
                    pdf_paths = _list_watched_pdfs(os.path.dirname(pdf_path))
                    nuovi_box = _calibra_box(pdf_paths, pdf_path, boxes_seed, ocr_cfg['dpi'])
```

- [ ] **Step 2: Verify the wiring with a monkeypatched calibrator**

This exercises the real `_rinomina_pdf` folder-scan-and-call wiring without needing `tkinter` or
`tesseract` (the fake calibrator returns `None`, so `_rinomina_pdf` stops right after the
cancellation branch, before it would reach OCR):

```bash
python3 - <<'EOF'
import sys, tempfile, os
sys.path.insert(0, '.')
from cmr_renamer import watcher
from PIL import Image

calls = []
def fake_calibra_box(pdf_paths, initial_path, boxes, dpi):
    calls.append((list(pdf_paths), initial_path, list(boxes), dpi))
    return None

watcher._calibra_box = fake_calibra_box

with tempfile.TemporaryDirectory() as d:
    target = os.path.join(d, 'DOC0001.pdf')
    Image.new('RGB', (300, 200), 'white').save(target)
    other = os.path.join(d, 'DOC0002.pdf')
    Image.new('RGB', (300, 200), 'white').save(other)

    ocr_cfg = {'boxes': [], 'show_rects': False, 'lang': 'eng', 'dpi': 150}
    name_cfg = {'max_length': 50, 'remove_leading_zeros': False}

    watcher._rinomina_pdf(target, ocr_cfg, name_cfg)

assert len(calls) == 1, calls
pdf_paths, initial_path, boxes, dpi = calls[0]
assert initial_path == target, initial_path
assert set(os.path.basename(p) for p in pdf_paths) == {'DOC0001.pdf', 'DOC0002.pdf'}, pdf_paths
assert dpi == 150, dpi
print('OK', calls)
EOF
```

Expected output: `OK` followed by the recorded call tuple.

- [ ] **Step 3: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Wire mandatory calibration to build the watched-folder file list"
```

---

### Task 5: Wire the tray "Ricalibra box" entry point

**Files:**
- Modify: `cmr_renamer/watcher.py` (`_recalibra` inside `_build_tray_icon`; tkinter.filedialog import)

**Interfaces:**
- Consumes: `_list_watched_pdfs` (Task 1), `_calibra_box(pdf_paths, initial_path, boxes, dpi)` (Task 3),
  `cartella` (already in `_build_tray_icon`'s closure — no signature change to `_build_tray_icon`
  itself).

- [ ] **Step 1: Replace `_recalibra`**

Replace:

```python
    def _recalibra(icon, item):
        if not _calibration_lock.acquire(blocking=False):
            print("⚠️ Calibrazione già in corso altrove: riprova più tardi.")
            return
        try:
            root = Tk()
            root.withdraw()
            pdf_path = askopenfilename(title="Seleziona un PDF da calibrare", filetypes=[("PDF", "*.pdf")])
            root.destroy()
            if not pdf_path:
                return
            immagini = convert_from_path(
                pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1,
                poppler_path=_get_poppler_path(),
            )
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
        finally:
            _calibration_lock.release()
```

with:

```python
    def _recalibra(icon, item):
        if not _calibration_lock.acquire(blocking=False):
            print("⚠️ Calibrazione già in corso altrove: riprova più tardi.")
            return
        try:
            pdf_paths = _list_watched_pdfs(cartella)
            if not pdf_paths:
                print("⚠️ Nessun PDF trovato nella cartella monitorata.")
                return
            boxes_seed = list(ocr_cfg['boxes'])
            while len(boxes_seed) < MIN_BOXES:
                boxes_seed.append(_default_box(len(boxes_seed)))
            nuovi_box = _calibra_box(pdf_paths, pdf_paths[0], boxes_seed, ocr_cfg['dpi'])
            if nuovi_box:
                ocr_cfg['boxes'] = nuovi_box
                _save_boxes_to_config(ocr_cfg['boxes'])
                print(f"✅ Nuove coordinate salvate → {ocr_cfg['boxes']}")
        except Exception as e:
            print(f"⚠️ Errore durante la ricalibrazione: {e}")
        finally:
            _calibration_lock.release()
```

- [ ] **Step 2: Remove the now-unused `askopenfilename` import**

Confirm it has no other callers, then remove it:

```bash
grep -n "askopenfilename" cmr_renamer/watcher.py
```

Expected output: only the `from tkinter.filedialog import askopenfilename` import line — no other
usages remain. Then replace:

```python
    from tkinter.filedialog import askopenfilename
```

by deleting that line entirely (it becomes dead after Step 1).

- [ ] **Step 3: Verify the wiring**

This uses the real `pystray.Icon`/`Menu` construction (available in this environment) with a
monkeypatched calibrator, invoking the actual "Ricalibra box" menu action without opening any real
tray or window:

```bash
python3 - <<'EOF'
import sys, threading, tempfile, os
sys.path.insert(0, '.')
from cmr_renamer import watcher
from PIL import Image

if not watcher.PYSTRAY_AVAILABLE:
    print("SKIP: pystray not installed in this environment")
    sys.exit(0)

calls = []
def fake_calibra_box(pdf_paths, initial_path, boxes, dpi):
    calls.append((list(pdf_paths), initial_path, list(boxes), dpi))
    return None

watcher._calibra_box = fake_calibra_box
icon_img = Image.new('RGB', (16, 16), 'white')
ocr_cfg = {'boxes': [], 'dpi': 150}

# Populated folder: calibrator should be invoked with both files, first one preselected.
with tempfile.TemporaryDirectory() as d:
    for name in ['b.pdf', 'a.pdf']:
        Image.new('RGB', (300, 200), 'white').save(os.path.join(d, name))
    tray = watcher._build_tray_icon(icon_img, ocr_cfg, '/tmp/log.txt', d, threading.Event())
    item = next(i for i in tray.menu.items if str(i.text) == 'Ricalibra box')
    item(tray)

assert len(calls) == 1, calls
pdf_paths, initial_path, boxes, dpi = calls[0]
assert [os.path.basename(p) for p in pdf_paths] == ['a.pdf', 'b.pdf'], pdf_paths
assert initial_path == pdf_paths[0], initial_path
print('OK populated folder', calls)

# Empty folder: calibrator must not be invoked at all.
calls.clear()
with tempfile.TemporaryDirectory() as d2:
    tray2 = watcher._build_tray_icon(icon_img, ocr_cfg, '/tmp/log.txt', d2, threading.Event())
    item2 = next(i for i in tray2.menu.items if str(i.text) == 'Ricalibra box')
    item2(tray2)
assert calls == [], calls
print('OK empty folder: no calibration attempted')
EOF
```

Expected output: `OK populated folder ...` then `OK empty folder: no calibration attempted`. If
`pystray` genuinely isn't available in whatever environment runs this, the script prints `SKIP` and
exits 0 rather than failing — treat a `SKIP` here as "not verified", not as a pass.

- [ ] **Step 4: Full-file syntax check**

```bash
python3 -m py_compile cmr_renamer/watcher.py
```

Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Wire tray recalibration to the watched-folder file list, drop file picker"
```

---

### Task 6: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the calibrator description**

Replace:

```markdown
`show_rects` is `True` in `config.ini` (opt-in recalibration). `_calibra_box` opens a Tk window with
the rendered page on a scrollable/zoomable `Canvas` (mouse wheel or +/− buttons, scaled around a
`base_scale` fit-to-screen and clamped by `MAX_ZOOM`/`MAX_DIM`) with colored, numbered selector
buttons (one per box, colors match the drawn rectangles) picking which box the next drag updates,
plus `+ Box`/`− Box` buttons (disabled at 5/2 respectively) to change the box count; saving persists
the new box list to `config.ini` via `_save_boxes_to_config` and applies it immediately to `ocr_cfg`
for the file being processed.
```

with:

```markdown
`show_rects` is `True` in `config.ini` (opt-in recalibration). `_calibra_box(pdf_paths, initial_path,
boxes, dpi)` opens a Tk window with the rendered page on a scrollable/zoomable `Canvas` (mouse wheel
or +/− buttons, scaled around a `base_scale` fit-to-screen and clamped by `MAX_ZOOM`/`MAX_DIM`),
plus a sidebar `Listbox` of every PDF in the watched folder (`_list_watched_pdfs`, sorted
alphabetically, `initial_path` preselected) so box placement can be checked live against multiple
real documents before saving — each page renders lazily via `_render_pdf_page` on first selection
and is cached for the rest of the session, and box coordinates are never remapped on switch (same
`dpi` for every render, so a misaligned box on another document is visible rather than hidden).
Colored, numbered selector buttons (one per box, colors match the drawn rectangles) pick which box
the next drag updates, plus `+ Box`/`− Box` buttons (disabled at 5/2 respectively) to change the box
count; saving persists the new box list to `config.ini` via `_save_boxes_to_config` and applies it
immediately to `ocr_cfg` for the file being processed.
```

- [ ] **Step 2: Update the tray menu description**

Replace:

```markdown
(`pystray`, guarded the same way as the `tkinter` import — missing `pystray` just means no tray,
not a crash) offers "Apri log", "Apri cartella monitorata", "Ricalibra box" (opens a file picker
and runs the same `_calibra_box` calibrator against a chosen PDF), and "Esci". This is the only way
```

with:

```markdown
(`pystray`, guarded the same way as the `tkinter` import — missing `pystray` just means no tray,
not a crash) offers "Apri log", "Apri cartella monitorata", "Ricalibra box" (scans the watched
folder for PDFs via `_list_watched_pdfs`, the same way mandatory first-run calibration does, and
opens the same `_calibra_box` calibrator against that list — no file picker; an empty folder just
prints a warning and does nothing), and "Esci". This is the only way
```

- [ ] **Step 3: Verify**

```bash
grep -n "opens a file picker\|_calibra_box\` opens a Tk window with$" CLAUDE.md
```

Expected: no matches (both old phrasings replaced).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Document multi-file calibrator sidebar in CLAUDE.md"
```

---

### Task 7: Manual GUI verification (human, on a machine with `tkinter`)

**Files:** none — this is a checklist, not a code change.

This plan's automated steps cannot exercise the actual Tk window (no `tkinter` in the sandbox it
was built in). Before considering this feature done, run these manually from source
(`python main.py`) on a machine with `tkinter`, `poppler`, and `tesseract` installed:

- [ ] Point the watched folder at a directory with 3-4 sample PDFs, delete `config.ini`'s `box*`
      keys to force mandatory calibration, and confirm: the sidebar lists all of them, the
      triggering file is pre-selected/highlighted, and clicking another filename swaps the
      background image while the drawn box rectangles stay in the same screen position.
- [ ] With boxes already configured, use the tray's "Ricalibra box" and confirm it opens directly
      against the watched folder's file list (no file-open dialog appears), with the first file
      alphabetically selected.
- [ ] Empty the watched folder and confirm "Ricalibra box" prints the warning to the log and does
      not open a window.
- [ ] Drag/add/remove boxes while a non-initial file is displayed, then save, and confirm
      `config.ini` reflects the change correctly (same as pre-existing calibrator behavior).
- [ ] Rename or delete a file in the watched folder from outside the app while the calibrator is
      open, then click it in the sidebar, and confirm the calibrator prints a warning and keeps
      showing the previously-displayed image instead of crashing.

- [ ] **Report results back** (pass/fail per bullet) before merging/shipping this change.
