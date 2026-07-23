# Design: Bundle poppler and tesseract into the exe

Date: 2026-07-23

## Purpose

CMR Renamer's OCR pipeline depends on two native command-line tools that Python only wraps:
`pdf2image` shells out to poppler (`pdftoppm`/`pdfinfo`) to rasterize PDF pages, and `pytesseract`
shells out to `tesseract.exe` to run OCR. Neither is bundled by pip, and neither is bundled by the
current PyInstaller `--onefile` build, so every end user must separately install poppler and
Tesseract and get them onto `PATH` before the exe works at all — a real "Unable to get page count.
Is poppler installed and in PATH?" failure mode a user just hit while testing the recalibrate
feature. This design vendors both tools' Windows binaries into the repo and wires them into the
build so the shipped exe is fully self-contained.

## 1. Source & vendoring

- **Poppler**: the community-standard portable Windows build from
  [`oschwartz10612/poppler-windows`](https://github.com/oschwartz10612/poppler-windows) releases
  (win64 only — this app already targets modern 64-bit Windows). Confirmed during design research:
  release `v26.02.0-0`'s zip contains a `Library/bin/` folder with all 39 `.exe`/`.dll` files
  (~24.6MB uncompressed) plus `share/poppler/COPYING`/`COPYING.gpl2` (license text). The whole
  `Library/bin/` folder is vendored as-is to `vendor/poppler/bin/` — cherry-picking a subset of
  DLLs risks a missing transitive dependency that can't be caught without a Windows machine to test
  on, so the safer choice is vendoring the complete, known-working folder.
- **Tesseract**: the UB-Mannheim Windows installer (`v5.4.0.20240606`, the same build the CI
  workflow already installs today via `choco install tesseract`) is an NSIS installer, not a
  portable zip — but `7z x <installer>.exe` cleanly extracts it (confirmed during design research:
  139 files, including `tesseract.exe` and ~60 dependency `.dll`s at the extraction root, plus a
  `tessdata/` folder). **The installer only bundles `eng.traineddata` + `osd.traineddata` — it does
  NOT include `ita.traineddata`** (additional languages are fetched separately by the installer's
  own UI at install time, not embedded in the exe). `ita.traineddata` is sourced instead from the
  official [`tesseract-ocr/tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast) repo
  (2.7MB). For consistency, `eng.traineddata` is taken from that same `tessdata_fast` repo rather
  than the installer's copy — confirmed byte-identical in size (4,113,088 bytes) to the installer's
  bundled file, so this is a lossless substitution that makes both languages consistently sourced.
  `osd.traineddata` (10.5MB, orientation/script detection) is kept even though this app's fixed-box
  cropping likely never needs it — its exclusion can't be verified without a working `tesseract`
  binary (see Testing), so it's included as cheap insurance rather than an unverified guess.
  Vendored to `vendor/tesseract/bin/` (binaries) and `vendor/tesseract/tessdata/` (the three
  `.traineddata` files), plus `vendor/tesseract/LICENSE` (Apache 2.0, fetched from the
  `tesseract-ocr/tesseract` repo).
- Both are vendored **once**, during implementation, as static committed files — not re-fetched or
  re-extracted on every CI run.
- A user who sets a different OCR language in `config.ini` (anything other than `eng`/`ita`) still
  needs a system-installed `tesseract` with that language's data on `PATH`, exactly like today.
  Bundling narrows, not eliminates, the external-dependency surface.

## 2. Git LFS

- `vendor/poppler/bin/*.exe`, `*.dll`, and `vendor/tesseract/tessdata/*.traineddata` are large
  binary files (tens of MB combined). Rather than bloating every clone's regular git history
  permanently, they're tracked via Git LFS.
- New `.gitattributes` entries:
  ```
  vendor/**/*.exe filter=lfs diff=lfs merge=lfs -text
  vendor/**/*.dll filter=lfs diff=lfs merge=lfs -text
  vendor/**/*.traineddata filter=lfs diff=lfs merge=lfs -text
  ```
- This makes Git LFS a new contributor requirement. `CLAUDE.md` gets a note that `git lfs install`
  is needed once per machine before cloning/building (GitHub-hosted Actions runners have `git-lfs`
  preinstalled, so CI needs no new tooling — just the checkout flag below).

## 3. Runtime resolution

Two new helpers in `cmr_renamer/watcher.py`, both mirroring the existing `_get_resource_path`/
`_is_frozen()` pattern already used for the tray icon:

```python
def _get_poppler_path() -> str | None:
    """Percorso ai binari poppler bundled quando frozen; None altrimenti (usa il PATH di sistema)."""
    if not _is_frozen():
        return None
    return _get_resource_path(os.path.join('vendor', 'poppler', 'bin'))


def _get_tesseract_cmd() -> str | None:
    """Percorso al tesseract.exe bundled quando frozen; None altrimenti (usa il PATH di sistema)."""
    if not _is_frozen():
        return None
    return _get_resource_path(os.path.join('vendor', 'tesseract', 'bin', 'tesseract.exe'))
```

- Both `convert_from_path` call sites (`_rinomina_pdf`, `_recalibra`) pass
  `poppler_path=_get_poppler_path()`.
- At startup, `run()` sets `pytesseract.pytesseract.tesseract_cmd` and
  `os.environ['TESSDATA_PREFIX']` **only when frozen** (guarding against overwriting a working
  system install when running from source):
  ```python
  if _is_frozen():
      tess_cmd = _get_tesseract_cmd()
      if tess_cmd:
          pytesseract.pytesseract.tesseract_cmd = tess_cmd
          os.environ['TESSDATA_PREFIX'] = _get_resource_path(os.path.join('vendor', 'tesseract', 'tessdata'))
  ```
- Running from source (any platform) is completely unaffected — same reliance on system-installed
  poppler/tesseract as today.

## 4. Build wiring

- PyInstaller command (CI workflow + `CLAUDE.md` local build command) gains two more `--add-data`
  flags, alongside the existing icon one:
  ```
  --add-data "vendor/poppler;vendor/poppler" --add-data "vendor/tesseract;vendor/tesseract"
  ```
- `.github/workflows/build_release.yml`'s checkout step gains `lfs: true` — without it, the
  exe would bundle empty LFS pointer files instead of the real binaries.
- The CI workflow's `choco install tesseract` step is **removed**. CI only builds the exe (it
  never runs it against a real PDF), and tesseract is now bundled from the already-vendored files
  rather than installed at build time — the choco step becomes dead weight once this lands.

## 5. Licensing

- Poppler (GPL): bundling the compiled binaries and invoking them via subprocess (not linking them
  into the app's own code) is "mere aggregation," not a derivative work — permissible, but the
  license text (`vendor/poppler/COPYING`) must ship alongside the binaries, and `README.md` gets a
  short note plus a link to poppler's own published source as the offer of source.
- Tesseract (Apache 2.0): permissive, no copyleft obligations — `vendor/tesseract/LICENSE` ships
  alongside the binary, with a brief attribution note in `README.md`.

## 6. Testing

No automated test suite exists in this repo (per `CLAUDE.md`) — verification is a mix of what's
actually runnable in this sandbox and what needs a real Windows machine. **Correction from initial
design research: this sandbox does NOT have a runnable `tesseract` CLI binary** (only its shared
libraries are installed as an OS package, no `tesseract` executable, and there's no passwordless
package-install available to add one) — so no local OCR execution is possible here at all, for
either poppler or tesseract. This narrows what's checkable before a human tests on Windows:

- **Runnable here**: confirming the vendored file sets are structurally correct — right files
  present at the right paths, right total size, valid zip/executable extraction — via `unzip -l`/
  `7z x`/`find`/`ls`, without executing any of the binaries.
- **Runnable here**: `_get_poppler_path()`/`_get_tesseract_cmd()`'s pure-Python frozen/non-frozen
  branching, via the same `sys.frozen`-mocking approach used for `_get_resource_path` previously.
- **Manual, Windows-only**: actually invoking the bundled `pdftoppm.exe`/`tesseract.exe` from a
  built exe, and confirming OCR still works with the `eng`+`ita`+`osd` tessdata set — this sandbox
  has no Windows environment and no working tesseract binary to cross-check against. Same category
  as the tray/calibrator manual verification already pending from the prior branch.

## Out of scope

- Bundling OCR languages beyond `eng`/`ita` — any other language remains a system-install
  requirement, unchanged from today.
- Automating the vendoring/extraction process (e.g. a script to re-fetch and re-extract on a
  version bump) — the binaries are vendored by hand for this pass; revisiting that tooling is a
  separate concern if poppler/tesseract versions need bumping later.
