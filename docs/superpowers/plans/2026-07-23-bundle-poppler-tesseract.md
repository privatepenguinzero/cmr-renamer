# Bundle Poppler and Tesseract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor poppler and tesseract Windows binaries into the repo (via Git LFS) and wire them into the PyInstaller build so the shipped exe needs no separately-installed, PATH-configured native dependencies.

**Architecture:** Two vendored binary trees (`vendor/poppler/`, `vendor/tesseract/`) tracked via Git LFS, two new resource-resolution helpers in `cmr_renamer/watcher.py` mirroring the existing `_get_resource_path`/`_is_frozen()` pattern, two new PyInstaller `--add-data` flags, and one CI workflow change (`lfs: true` on checkout, removing the now-redundant `choco install tesseract` step).

**Tech Stack:** Python 3.10+, `pdf2image` (`poppler_path` parameter), `pytesseract` (`tesseract_cmd` attribute, `TESSDATA_PREFIX` env var), Git LFS, PyInstaller `--add-data`.

## Global Constraints

- No test suite, linter, or formatter exists in this repo (per `CLAUDE.md`) — verification uses `python3 -c` scripts with `assert`, plus structural checks (`find`/`ls`/`unzip -l`) for the vendored files. Do not add `pytest`.
- **This sandbox has no runnable `tesseract` CLI binary** (only OS-level shared libs are installed, no executable, no passwordless package install available) and **no Windows environment** — no task in this plan can execute the vendored binaries. Every task's verification is limited to structural checks and pure-Python logic; actually running the bundled `.exe`s is an explicit manual step for a human on Windows.
- Poppler source: [`oschwartz10612/poppler-windows`](https://github.com/oschwartz10612/poppler-windows) release `v26.02.0-0`, asset `Release-26.02.0-0.zip`. Vendor the entire `Library/bin/` folder (39 files, ~24.6MB) — do not cherry-pick a subset of DLLs.
- Tesseract source: UB-Mannheim installer `v5.4.0.20240606`
  (`tesseract-ocr-w64-setup-5.4.0.20240606.exe`), extracted with `7z x`. The installer only bundles
  `eng.traineddata` + `osd.traineddata` — **`ita.traineddata` must come from
  [`tesseract-ocr/tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast)** (confirmed
  byte-identical in size to the installer's `eng.traineddata`, so `eng.traineddata` is also taken
  from `tessdata_fast` for consistency, not from the installer). Keep `osd.traineddata` (cheap
  insurance, since its necessity can't be verified in this sandbox).
- Git LFS tracks `vendor/**/*.exe`, `vendor/**/*.dll`, `vendor/**/*.traineddata` — set up `.gitattributes` **before** any vendor files are added/committed, so they're captured by LFS from the first commit (not retrofitted via `git lfs migrate`).
- Running from source (any platform) must be completely unaffected — same reliance on system-installed poppler/tesseract as today. Bundling only takes effect when `_is_frozen()` is true.
- Poppler is GPL — its license text (`COPYING`/`COPYING.gpl2`) must be vendored alongside the binaries and referenced in `README.md`. Tesseract is Apache 2.0 — its `LICENSE` file must be vendored and referenced in `README.md`.

---

### Task 1: Set up Git LFS tracking

**Files:**
- Create: `.gitattributes`

**Interfaces:**
- Produces: LFS tracking patterns that Tasks 2 and 3's `git add` commands rely on to capture the vendored binaries as LFS objects, not regular git blobs.

- [ ] **Step 1: Confirm git-lfs is available**

Run: `git lfs version`
Expected: version output (e.g. `git-lfs/3.x.x`). If this fails with "command not found", STOP and report BLOCKED — the rest of this plan depends on Git LFS being installed.

- [ ] **Step 2: Install LFS hooks for this repo**

Run: `git lfs install`
Expected: `Updated git hooks.` / `Git LFS initialized.`

- [ ] **Step 3: Create `.gitattributes`**

Create `.gitattributes` at the repo root:
```
vendor/**/*.exe filter=lfs diff=lfs merge=lfs -text
vendor/**/*.dll filter=lfs diff=lfs merge=lfs -text
vendor/**/*.traineddata filter=lfs diff=lfs merge=lfs -text
```

- [ ] **Step 4: Verify tracking is registered**

Run: `git check-attr filter -- vendor/poppler/bin/pdftoppm.exe vendor/tesseract/tessdata/eng.traineddata`
Expected:
```
vendor/poppler/bin/pdftoppm.exe: filter: lfs
vendor/tesseract/tessdata/eng.traineddata: filter: lfs
```
(This works even though those files don't exist yet — `git check-attr` only checks the pattern match, not file existence.)

- [ ] **Step 5: Commit**

```bash
git add .gitattributes
git commit -m "Track vendored binary dependencies with Git LFS"
```

---

### Task 2: Vendor poppler binaries

**Files:**
- Create: `vendor/poppler/bin/*` (39 files: `.exe`s and `.dll`s from the poppler-windows release)
- Create: `vendor/poppler/COPYING`
- Create: `vendor/poppler/COPYING.gpl2`

**Interfaces:**
- Consumes: `.gitattributes` from Task 1 (must already be committed so these files are captured by LFS on add).
- Produces: `vendor/poppler/bin/` directory, consumed by Task 4 (`_get_poppler_path()`) and Task 5 (PyInstaller `--add-data`).

- [ ] **Step 1: Download and inspect the release zip**

```bash
mkdir -p /tmp/vendor-poppler
cd /tmp/vendor-poppler
curl -sL -o poppler.zip "https://github.com/oschwartz10612/poppler-windows/releases/download/v26.02.0-0/Release-26.02.0-0.zip"
unzip -l poppler.zip | grep "Library/bin/" | grep -v "/$" | wc -l
```
Expected: `39` (confirms the release still has the same file count as when this plan was written; if it doesn't match, the release may have changed — proceed with whatever the actual count is, it's not a hard requirement, just a sanity check).

- [ ] **Step 2: Extract the needed folders**

```bash
cd /tmp/vendor-poppler
unzip -q poppler.zip "poppler-*/Library/bin/*" "poppler-*/share/poppler/COPYING*"
find . -iname "COPYING*"
```
Expected: two paths ending in `share/poppler/COPYING` and `share/poppler/COPYING.gpl2`.

- [ ] **Step 3: Copy into the repo's vendor tree**

Run from the repo root (`/var/home/icenoir/coding/test/cmr-renamer`):
```bash
mkdir -p vendor/poppler/bin
cp /tmp/vendor-poppler/poppler-*/Library/bin/* vendor/poppler/bin/
cp /tmp/vendor-poppler/poppler-*/share/poppler/COPYING vendor/poppler/COPYING
cp /tmp/vendor-poppler/poppler-*/share/poppler/COPYING.gpl2 vendor/poppler/COPYING.gpl2
ls vendor/poppler/bin | wc -l
```
Expected: same file count as Step 1's `unzip -l` count.

- [ ] **Step 4: Verify pdftoppm.exe and pdfinfo.exe are present (the two binaries pdf2image actually calls)**

Run: `ls vendor/poppler/bin/pdftoppm.exe vendor/poppler/bin/pdfinfo.exe`
Expected: both paths listed, no "No such file" errors.

- [ ] **Step 5: Verify LFS is tracking the binary files (not storing them as regular blobs)**

```bash
git add vendor/poppler/
git status --porcelain vendor/poppler/ | head -3
git lfs status
```
Expected: `git lfs status` lists the `.exe`/`.dll` files under "Git LFS objects to be committed" (not "Git blobs" — if a file shows up as a regular blob instead of an LFS pointer, `.gitattributes` from Task 1 wasn't picked up; re-run `git rm --cached` on it and `git add` again).

- [ ] **Step 6: Commit**

```bash
git commit -m "Vendor poppler Windows binaries (v26.02.0-0) for exe bundling"
```

---

### Task 3: Vendor tesseract binaries and tessdata

**Files:**
- Create: `vendor/tesseract/bin/tesseract.exe` and its dependency `.dll`s (~60 files)
- Create: `vendor/tesseract/tessdata/eng.traineddata`
- Create: `vendor/tesseract/tessdata/ita.traineddata`
- Create: `vendor/tesseract/tessdata/osd.traineddata`
- Create: `vendor/tesseract/LICENSE`

**Interfaces:**
- Consumes: `.gitattributes` from Task 1.
- Produces: `vendor/tesseract/bin/`, `vendor/tesseract/tessdata/`, consumed by Task 4 (`_get_tesseract_cmd()`) and Task 5 (PyInstaller `--add-data`).

- [ ] **Step 1: Download and extract the installer**

```bash
mkdir -p /tmp/vendor-tesseract
cd /tmp/vendor-tesseract
curl -sL -o tesseract-setup.exe "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
7z x tesseract-setup.exe -oextract -y > /dev/null
ls extract/tesseract.exe extract/tessdata/eng.traineddata extract/tessdata/osd.traineddata extract/doc/LICENSE
```
Expected: all four paths listed, no "No such file" errors.

- [ ] **Step 2: Download the correctly-sourced eng/ita tessdata (tessdata_fast, not the installer's copy)**

```bash
cd /tmp/vendor-tesseract
curl -sL -o eng.traineddata "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata"
curl -sL -o ita.traineddata "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/ita.traineddata"
ls -la eng.traineddata ita.traineddata
```
Expected: `eng.traineddata` is exactly 4113088 bytes; `ita.traineddata` is roughly 2.7MB (exact size may drift slightly if the upstream file has been updated since this plan was written — that's fine, just confirm it downloaded and is non-empty).

- [ ] **Step 3: Copy the binaries (everything at the extraction root except subfolders) into the repo's vendor tree**

Run from the repo root (`/var/home/icenoir/coding/test/cmr-renamer`):
```bash
mkdir -p vendor/tesseract/bin vendor/tesseract/tessdata
find /tmp/vendor-tesseract/extract -maxdepth 1 -type f \( -iname "*.exe" -o -iname "*.dll" \) -exec cp {} vendor/tesseract/bin/ \;
ls vendor/tesseract/bin/tesseract.exe
```
Expected: `vendor/tesseract/bin/tesseract.exe` exists (this excludes the many training/utility `.exe`s like `cntraining.exe`, `lstmtraining.exe`, etc. that aren't needed at runtime — the `find` command above only grabs top-level `.exe`/`.dll` files, which is everything `tesseract.exe` needs; the utility tools live alongside it but this app never calls them, so leaving them out is fine and saves space. **Note for whoever runs this:** if you'd rather keep it simple and avoid guessing which utility exes are safe to drop, copying the entire extraction root's flat file list — `find /tmp/vendor-tesseract/extract -maxdepth 1 -type f -iname "*.exe" -o -iname "*.dll"` already does exactly that, it does NOT recurse into `tessdata/`/`doc/`/`$PLUGINSDIR`/etc., so this command is already the complete, correct, minimal set).

- [ ] **Step 4: Copy the tessdata files and license**

```bash
cp /tmp/vendor-tesseract/osd.traineddata vendor/tesseract/tessdata/ 2>/dev/null || cp /tmp/vendor-tesseract/extract/tessdata/osd.traineddata vendor/tesseract/tessdata/
cp /tmp/vendor-tesseract/eng.traineddata vendor/tesseract/tessdata/
cp /tmp/vendor-tesseract/ita.traineddata vendor/tesseract/tessdata/
cp /tmp/vendor-tesseract/extract/doc/LICENSE vendor/tesseract/LICENSE
ls vendor/tesseract/tessdata/
```
Expected: `eng.traineddata`, `ita.traineddata`, `osd.traineddata` all listed.

- [ ] **Step 5: Verify LFS is tracking the binary/tessdata files**

```bash
git add vendor/tesseract/
git lfs status
```
Expected: the `.exe`/`.dll`/`.traineddata` files listed under "Git LFS objects to be committed", same check as Task 2 Step 5.

- [ ] **Step 6: Commit**

```bash
git commit -m "Vendor tesseract Windows binaries and eng/ita/osd tessdata for exe bundling"
```

---

### Task 4: Runtime resolution — wire poppler_path and tesseract_cmd

**Files:**
- Modify: `cmr_renamer/watcher.py` — add two helpers near `_get_resource_path` (currently lines 60-63), update both `convert_from_path` call sites (currently lines 526 and 556), update `run()` (currently around lines 691-694)

**Interfaces:**
- Consumes: `_get_resource_path`, `_is_frozen` (already exist), `vendor/poppler/bin/` from Task 2, `vendor/tesseract/bin/tesseract.exe` and `vendor/tesseract/tessdata/` from Task 3.
- Produces: `_get_poppler_path() -> str | None`, `_get_tesseract_cmd() -> str | None` — not consumed by any later task in this plan (this is the last code-touching task).

- [ ] **Step 1: Add the two resolution helpers**

Find (in `cmr_renamer/watcher.py`):
```python
def _get_resource_path(relative_path: str) -> str:
    """Risolve un percorso di risorsa bundled, sia da sorgente che da frozen (PyInstaller onefile)."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative_path)
```
Replace with:
```python
def _get_resource_path(relative_path: str) -> str:
    """Risolve un percorso di risorsa bundled, sia da sorgente che da frozen (PyInstaller onefile)."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, relative_path)


def _get_poppler_path() -> "str | None":
    """Percorso ai binari poppler bundled quando frozen; None altrimenti (usa il PATH di sistema)."""
    if not _is_frozen():
        return None
    return _get_resource_path(os.path.join('vendor', 'poppler', 'bin'))


def _get_tesseract_cmd() -> "str | None":
    """Percorso al tesseract.exe bundled quando frozen; None altrimenti (usa il PATH di sistema)."""
    if not _is_frozen():
        return None
    return _get_resource_path(os.path.join('vendor', 'tesseract', 'bin', 'tesseract.exe'))
```

- [ ] **Step 2: Wire `poppler_path` into both `convert_from_path` call sites**

This exact line appears twice in `cmr_renamer/watcher.py` (once inside `_recalibra`, once inside `_rinomina_pdf`) — apply this same replacement to **both** occurrences:

Find:
```python
            immagini = convert_from_path(pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1)
```
Replace with:
```python
            immagini = convert_from_path(
                pdf_path, dpi=ocr_cfg['dpi'], first_page=1, last_page=1,
                poppler_path=_get_poppler_path(),
            )
```

(Note: in `_rinomina_pdf` this line is indented with 8 spaces rather than 12 — match whatever indentation the surrounding code already uses at each of the two call sites; the content change is identical, only the leading whitespace differs between the two.)

- [ ] **Step 3: Set `tesseract_cmd`/`TESSDATA_PREFIX` at startup when frozen**

Find (in `run()`):
```python
    else:
        # Normal Python execution → use console as-is
        cfg = load_or_create_config(config_path=config_path)

    # ── Parse config ───────────────────────────────────────
```
Replace with:
```python
    else:
        # Normal Python execution → use console as-is
        cfg = load_or_create_config(config_path=config_path)

    # ── Bundled native tools (frozen only) ──────────────────
    if _is_frozen():
        tess_cmd = _get_tesseract_cmd()
        if tess_cmd:
            pytesseract.pytesseract.tesseract_cmd = tess_cmd
            os.environ['TESSDATA_PREFIX'] = _get_resource_path(os.path.join('vendor', 'tesseract', 'tessdata'))

    # ── Parse config ───────────────────────────────────────
```

- [ ] **Step 4: Verify the module still imports cleanly**

Run: `python3 -c "import cmr_renamer.watcher; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Verify the frozen/non-frozen branching in isolation**

```bash
python3 -c "
import sys
from cmr_renamer import watcher

assert watcher._get_poppler_path() is None, 'not frozen: should be None'
assert watcher._get_tesseract_cmd() is None, 'not frozen: should be None'

sys.frozen = True
try:
    p = watcher._get_poppler_path()
    t = watcher._get_tesseract_cmd()
    assert p.endswith('vendor/poppler/bin') or p.endswith('vendor\\\\poppler\\\\bin'), p
    assert t.endswith('vendor/tesseract/bin/tesseract.exe') or t.endswith('vendor\\\\tesseract\\\\bin\\\\tesseract.exe'), t
finally:
    del sys.frozen

print('OK')
"
```
Expected: `OK`

- [ ] **Step 6: Verify both convert_from_path call sites were updated**

Run: `grep -c "poppler_path=_get_poppler_path()" cmr_renamer/watcher.py`
Expected: `2`

- [ ] **Step 7: Commit**

```bash
git add cmr_renamer/watcher.py
git commit -m "Resolve bundled poppler/tesseract paths when frozen, fall back to system PATH from source"
```

---

### Task 5: Build wiring — PyInstaller flags, CI checkout, remove redundant tesseract install

**Files:**
- Modify: `.github/workflows/build_release.yml`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `vendor/poppler/` (Task 2), `vendor/tesseract/` (Task 3).
- Produces: nothing consumed by later tasks — this is a build-config-only task.

- [ ] **Step 1: Add `lfs: true` to the CI checkout step**

Find (in `.github/workflows/build_release.yml`):
```yaml
      # 1️⃣ Checkout repository
      - name: Checkout repository
        uses: actions/checkout@v7.0.1
        with:
          fetch-depth: 0
```
Replace with:
```yaml
      # 1️⃣ Checkout repository
      - name: Checkout repository
        uses: actions/checkout@v7.0.1
        with:
          fetch-depth: 0
          lfs: true
```

- [ ] **Step 2: Remove the now-redundant `choco install tesseract` step**

Find (in `.github/workflows/build_release.yml`):
```yaml
      # 6️⃣ Install Tesseract OCR (required by pytesseract)
      - name: Install Tesseract OCR
        run: choco install -y tesseract
        shell: cmd

      # 7️⃣ Build executable
```
Replace with:
```yaml
      # 6️⃣ Build executable
```

(This removes the step entirely — tesseract is now bundled from the vendored files rather than installed at build time. Renumber the following step's comment from "7️⃣" to "6️⃣" as shown; leave its actual `name:`/`run:`/`shell:` content unchanged except for the `--add-data` flags added in Step 3 below.)

- [ ] **Step 3: Add the two new `--add-data` flags to the CI build command**

Find (in `.github/workflows/build_release.yml`, the build step you just renumbered in Step 2):
```yaml
      - name: Build executable
        run: |
          uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" main.py
        shell: pwsh
```
Replace with:
```yaml
      - name: Build executable
        run: |
          uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" --add-data "vendor/poppler;vendor/poppler" --add-data "vendor/tesseract;vendor/tesseract" main.py
        shell: pwsh
```

- [ ] **Step 4: Update the local build command in CLAUDE.md to match**

Find (in `CLAUDE.md`):
```
uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" main.py
```
Replace with:
```
uv run -- pyinstaller --onefile --windowed --name cmr-renamer --icon=assets/icon.ico --add-data "assets/icon.ico;assets" --add-data "vendor/poppler;vendor/poppler" --add-data "vendor/tesseract;vendor/tesseract" main.py
```

- [ ] **Step 5: Add a Git LFS prerequisite note to CLAUDE.md**

Find (in `CLAUDE.md`):
```
There is no test suite, linter, or formatter configured in this repo — don't assume `pytest`/`ruff`
exist. Tesseract OCR must be installed on the system (`pytesseract` only wraps the binary); the CI
workflow installs it via `choco install tesseract` on windows-latest.
```
Replace with:
```
There is no test suite, linter, or formatter configured in this repo — don't assume `pytest`/`ruff`
exist. Poppler and Tesseract are vendored under `vendor/` (via Git LFS) and bundled into the built
exe — see "Bundled native dependencies" below. Running from source still needs both installed
system-wide and on `PATH`, exactly as before this bundling was added; the CI workflow no longer
installs Tesseract via choco since the build now uses the vendored copy instead.

**Git LFS is required to clone/build this repo**: run `git lfs install` once per machine before
cloning, otherwise `vendor/` will contain empty LFS pointer files instead of the real binaries.
```

- [ ] **Step 6: Verify the workflow YAML still parses**

```bash
python3 -c "
import yaml
with open('.github/workflows/build_release.yml') as f:
    doc = yaml.safe_load(f)
assert doc['jobs']['build']['steps'][0]['with']['lfs'] is True
names = [s.get('name') for s in doc['jobs']['build']['steps']]
assert 'Install Tesseract OCR' not in names, names
print('OK', names)
" 2>&1 || echo "pyyaml not installed — visually re-read the file instead and confirm the same two things by eye: step 1 has lfs: true, and no step is named 'Install Tesseract OCR'"
```
Expected: either `OK` followed by the step name list, or (if `pyyaml` isn't available) a manual visual re-check per the fallback message.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/build_release.yml CLAUDE.md
git commit -m "Wire vendored poppler/tesseract into the PyInstaller build, drop choco tesseract install"
```

---

### Task 6: Licensing documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Read the current README to find a sensible insertion point**

Run: `cat README.md`

- [ ] **Step 2: Add a licensing/attribution section**

Add this section to `README.md` (place it near the end, e.g. before or after any existing "License" section — if one already exists, add this as a subsection within it rather than a duplicate top-level heading):
```markdown
## Bundled third-party binaries

The built executable vendors two native tools so end users don't need to install anything
separately:

- **[Poppler](https://poppler.freedesktop.org/)** (GPL) — used by `pdf2image` to rasterize PDF
  pages. The compiled binaries are redistributed as-is and invoked as external processes (not
  linked into this project's code); license text is included at `vendor/poppler/COPYING` and
  `vendor/poppler/COPYING.gpl2`.
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** (Apache License 2.0) — used by
  `pytesseract` to perform OCR. License text is included at `vendor/tesseract/LICENSE`. Only
  `eng`/`ita`/`osd` language data is bundled; other OCR languages require a system-installed
  Tesseract with that language's data.
```

- [ ] **Step 3: Verify the section was added**

Run: `grep -n "Bundled third-party binaries" README.md`
Expected: one match.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document bundled poppler/tesseract licensing in README"
```
