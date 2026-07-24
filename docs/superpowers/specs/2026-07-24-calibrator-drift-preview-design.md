# Design: Show drift-corrected box preview in the calibrator

Date: 2026-07-24

## Purpose

Scan drift correction (`_detect_content_anchor` / `_resolve_crop_boxes`, shipped in
`2026-07-24-scan-drift-anchor-design.md`) works correctly when actually processing files, but the
box calibrator's multi-file sidebar still always shows boxes at their raw calibrated coordinates —
by design, so misalignment is visible. Now that correction exists, the user wants to also see the
*corrected* (adapted) box position when browsing other files in the sidebar, to visually confirm
the correction actually re-aligns boxes across multiple real documents before saving.

## Scope

- Calibrator-only change (`_calibra_box` in `cmr_renamer/watcher.py`). No change to
  `_rinomina_pdf`'s actual processing behavior, which already applies correction correctly.
- The preview always shows the corrected position — no raw/corrected toggle.
- The fixed reference point for computing preview correction is the content anchor of
  `initial_path`'s image (the file that triggered calibration, or the first file alphabetically for
  the tray's "Ricalibra box") — computed once when the calibrator opens, independent of whatever
  anchor may already be saved in `config.ini` from a previous calibration.
- Silent fallback: when correction can't be computed for the currently displayed file (reference
  anchor undetectable, current file's anchor undetectable, or the shift exceeds the existing 15mm
  plausibility limit), that file's preview simply shows the raw calibrated box positions — no
  visual indicator, consistent with `_resolve_crop_boxes`'s existing fail-soft behavior.

## 1. Shared shift computation

Extract `_compute_anchor_shift(current: tuple|None, reference: tuple|None, dpi: int) -> tuple[int, int]`
from the shift/plausibility logic already inside `_resolve_crop_boxes`: returns `(0, 0)` if either
anchor is `None` or the shift exceeds `MAX_ANCHOR_SHIFT_MM`, otherwise the real `(dx, dy)`.
`_resolve_crop_boxes` is refactored to call this helper (same behavior, same log warnings — this is
a pure internal extraction, not a behavior change), and the calibrator reuses it for preview
purposes without printing anything (no indicator, per the scope above).

## 2. Reference anchor and per-file preview shift

- Right after the calibrator loads `initial_path`'s image, it computes
  `state['reference_anchor'] = _detect_content_anchor(state['img'])` once for the session.
- A new `update_preview_shift()` step computes
  `state['preview_shift'] = _compute_anchor_shift(_detect_content_anchor(state['img']), state['reference_anchor'], dpi)`
  and is called once at startup and again every time the displayed file changes (`on_file_select`).
- The reference file itself always resolves to `(0, 0)` shift (comparing its anchor to itself).

## 3. Drawing and editing with the shift applied

- `draw_box`/`draw_all_boxes` add `state['preview_shift']` to each box's stored (raw) coordinates
  before scaling for the canvas — every file's overlay now shows the position boxes would actually
  crop to after correction.
- `on_release` (finishing a drag) does the inverse: it computes the dropped position in image-pixel
  space as today, then *subtracts* `state['preview_shift']` before storing it into `state['boxes']`.
  This keeps the stored box in the same reference frame regardless of which file was on screen while
  dragging — what you see is where the box ends up, but the saved coordinate stays correctly
  translated back to the reference frame so it doesn't end up double-shifted when viewed elsewhere
  (or saved).

## Error handling

- Reference anchor undetectable (near-blank `initial_path` page) → `state['reference_anchor']` is
  `None`, `_compute_anchor_shift` always returns `(0, 0)`, every file's preview silently shows raw
  boxes — identical to today's behavior, no crash, no special-case code needed.
- Any other file's anchor undetectable, or shift beyond the plausibility limit → same silent
  `(0, 0)` fallback for that file only; other files keep showing their own corrected preview.

## Testing

No automated test suite exists in this repo (per `CLAUDE.md`). `_compute_anchor_shift` is a pure
function, fully testable without `tkinter` (unlike the rest of the calibrator). The
draw/drag-with-shift behavior itself requires manual verification on a machine with `tkinter`,
same limitation as the previous two calibrator changes.

## Out of scope

- A raw/corrected toggle.
- Any visual indicator for the silent fallback case.
- Using an existing saved `config.ini` anchor as the preview reference instead of `initial_path`.
- Any change to `_rinomina_pdf`'s actual processing/correction behavior.
