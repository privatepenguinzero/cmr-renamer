# Design: Scan drift correction via content anchor

Date: 2026-07-24

## Purpose

Scans arriving from a photocopier can shift a few millimeters vertically and/or horizontally
compared to how the page looked when the OCR boxes were calibrated (the scanner glass/feeder
doesn't always capture the page in exactly the same position). When that happens, calibrated boxes
can miss their intended content even though the document itself is unchanged. Rotation observed in
practice is negligible (1-2 degrees), so this is a translation-only problem, not a deskew problem.

This design adds automatic drift correction: at calibration time, the tool records where the
page's content ("black") starts from the top and from the left — the *content anchor*. At
processing time, it re-detects that same anchor on the actual scan and shifts all OCR boxes by the
difference before cropping, so the boxes stay locked onto the content instead of the raw page
coordinates.

## Scope

- Translation-only correction (X and Y shift). No rotation/deskew correction — out of scope per
  the negligible rotation observed.
- Fully automatic: no new calibration UI step, no config toggle. The anchor is computed
  automatically every time boxes are calibrated/saved, and applied automatically every time a file
  is processed, provided a reference anchor exists.
- PIL-only implementation, consistent with the rest of the OCR pipeline (`_preprocess_for_ocr`) —
  no new dependency (no numpy, no OpenCV).
- Existing `config.ini` files without a stored anchor keep working exactly as today (no
  correction attempted) until the next successful calibration establishes one.

## 1. Content anchor detection

New helper `_detect_content_anchor(img: Image.Image) -> tuple[int, int] | None`:

- Convert to grayscale and binarize with the same fixed threshold (`128`) already used by
  `_preprocess_for_ocr`, so "dark" has one consistent meaning across the codebase.
- Compute a per-row darkness profile by resizing the binarized image down to `(1, height)` using
  the `BOX` resampling filter — this averages each row's pixels into a single value in one fast,
  C-level PIL call (no numpy, no per-pixel Python loop over the full image). Do the same down to
  `(width, 1)` for a per-column profile.
- Scanning from the start of each profile (top for rows, left for columns), find the first
  position where the dark-pixel fraction is at least a minimum density (3%) *and* stays at or above
  that density for a minimum run of consecutive positions (4px) — this is what filters out isolated
  noise (a staple hole, a dust speck) from a genuine content edge (a table border, a text block).
- Returns `(anchor_x, anchor_y)` — the detected left and top content edges — or `None` if no such
  run is found anywhere in the page (near-blank page).

## 2. Capturing the reference anchor at calibration time

- `_calibra_box`'s `on_save()` now also computes `anchor = _detect_content_anchor(state['img'])` —
  deliberately the image *currently displayed* when "Salva" is pressed, not necessarily
  `initial_path`'s image, since the multi-file sidebar (added in the previous feature) lets the user
  save while looking at whichever file they were comparing against. The reference anchor must match
  whatever page the boxes were visually tuned against.
- `_calibra_box`'s return value changes from `list | None` to `{'boxes': list, 'anchor': tuple | None} | None`
  (still `None` on cancel; `anchor` can independently be `None` if detection failed even on the
  calibration image, e.g. a near-blank page — calibration still succeeds, just without drift
  correction active).
- `_save_boxes_to_config` is renamed `_save_calibration_to_config(boxes, anchor)` (it now saves more
  than just box coordinates) and additionally writes `anchor_x`/`anchor_y` to `config.ini`'s
  `[OCR]` section, or removes those keys if `anchor` is `None` (mirroring the existing cleanup of
  stale `boxN` keys when the box count shrinks).
- Both calibration call sites (`_rinomina_pdf`'s mandatory-calibration branch, and the tray's
  `_recalibra`) are updated to unpack the new `{'boxes', 'anchor'}` shape and pass both to
  `_save_calibration_to_config`.

## 3. Applying the correction during processing

- Config loading gains `ocr_cfg['anchor']`: `(int(anchor_x), int(anchor_y))` if both keys are
  present in `config.ini`, else `None` — same optional-key fallback pattern already used for
  `prefix`, `box1..5`, and `show_rects`.
- In `_rinomina_pdf`, after rendering the page and resolving `ocr_cfg['boxes']` (i.e. after any
  calibration branch has run), and only if `ocr_cfg['anchor']` is not `None`:
  - Run `_detect_content_anchor(img)` on the current page.
  - If it returns `None`, or the resulting shift on either axis exceeds a fixed plausibility limit
    (15mm, converted to pixels via the configured `dpi`), log a warning and use the boxes
    unmodified — the file is still processed and renamed, never blocked.
  - Otherwise, shift every box by `(current_anchor - reference_anchor)` before cropping. This shift
    is computed fresh per file and is never written back to `config.ini` — the stored calibration
    stays the fixed reference; only the in-memory crop coordinates for that one file move.

## Error handling

- Near-blank page at calibration time → calibration still saves boxes; `anchor` stored as absent;
  drift correction stays inactive until a future successful calibration detects one.
- Near-blank page or implausible shift at processing time → warning logged, original calibrated
  box positions used, file still renamed normally.
- Config predating this feature (no `anchor_x`/`anchor_y`) → `ocr_cfg['anchor']` is `None`, no
  detection is attempted at all, behavior is identical to today.

## Testing

No automated test suite exists in this repo (per `CLAUDE.md`). Verification is manual:

- Calibrate against one scan, then process a second scan of the same document type shifted a few
  millimeters vertically and/or horizontally, and confirm the renamed output matches (boxes
  followed the content).
- Process a near-blank/mostly-white page and confirm it falls back to uncorrected boxes with a
  logged warning instead of crashing or misplacing crops wildly.
- Process a scan with a deliberately extreme/implausible shift and confirm the plausibility limit
  triggers the same safe fallback.
- Confirm an existing `config.ini` from before this feature (no `anchor_x`/`anchor_y`) continues to
  process files exactly as before, with no correction attempted.

## Out of scope

- Rotation/deskew correction.
- Per-file configurable detection thresholds (density, run length, plausibility limit) — fixed
  internal constants, consistent with other hardcoded tuning values in the OCR pipeline (e.g. the
  128 binarization threshold).
- A config toggle to disable the feature — always active once a reference anchor exists, per
  decision above.
- Any change to the multi-file calibrator sidebar itself, beyond using whichever image is on
  screen at save time as the anchor reference.
