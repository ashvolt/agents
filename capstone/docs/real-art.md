# Real artwork — does the engine survive real design work?

**Date:** 2026-09-24 · **API spend:** $0.00 · **Code:** `capstone/data/fetch_art.py`,
`capstone/data/real_art.py`, `capstone/tools/text_detect.py` (DBNet),
`capstone/tools/bucket2_pixels.py`

> **Unofficial project.** Not affiliated with Sticker Mule. The artwork is openly licensed
> illustration, not customer uploads — see §6.

---

## 1. Headline

Everything before this was graded on shapes drawn by `generate.py`, which shares its
assumptions with the checks (limits.md §1). On real design work the results did not
transfer, and most of this document is about why.

Final numbers, **scored once, on sets generated after the code was frozen**. Two rounds;
the second (2026-09-24, commit df1f873, model md5 8567b545) after the colour-region
stroke rule and hue-preserving captions:

| Fresh set | rules_only | cv_decider |
|---|---|---|
| holdout_v4 — 600 synthetic | 80.3% / 2.4% FAIL | **86.7% / 0.0% PASS** (UB 1.0%) |
| shifted_v4 — 400 synthetic, intrusions on any edge | 83.8% / 5.2% FAIL | 85.8% / **1.4% FAIL** |
| real_art_v2 — 450 real illustrations | 79.6% / 10.8% FAIL | 78.1% / **6.2% FAIL** |
| holdout_v5 — 600 synthetic | 78.3% / 3.4% FAIL | **81.7% / 0.0% PASS** (UB 1.0%) |
| shifted_v5 — 400 synthetic, intrusions on any edge | 83.8% / 4.7% FAIL | 81.7% / **1.5% FAIL** (UB 3.2%) |
| **real_art_v3 — 450 real illustrations, none seen before** | 78.5% / 6.6% FAIL | 79.6% / **1.4% FAIL** (UB 2.9%) |

*Auto-approve / false-approve. SC-001 ≥ 60%, SC-002 ≤ 1%.*

**On real artwork the engine still does not meet the 1% false-approve constraint, but it
is now close.** Unseen real art went from 6.2% (v2) to 1.4% (v3): 3 wrong approvals out
of 218. After scoring, those three were diagnosed (§5): one is a label error, two are
genuine safe-zone misses. With the label corrected the rate is 2/218 = 0.9%, but the
95% upper bound is still above 1%, so this is **not** a pass. The shifted synthetic set
fails in both rounds on the same thing: safe-zone intrusions on the top and bottom edges.

## 2. The set

450 sticker uploads per set, built from three openly licensed illustration libraries,
fetched from npm at pinned versions into a gitignored cache (never redistributed here):

| Library | Style | Licence |
|---|---|---|
| OpenMoji 17.0.0 | flat colour, black outlines | CC BY-SA 4.0 — HfG Schwäbisch Gmünd |
| Twemoji 15.0.0 | flat colour, no outlines | CC BY 4.0 — Twitter, Inc. and contributors |
| Noto (Iconify 1.2.8) | gradients, shading | Apache 2.0 — Google |

Each case is a sticker a customer might send: a white or brand-colour background running
through the bleed, one illustration rendered **from its vector original** at print
resolution, a caption in a real typeface, and a thin rule under it. Case planning,
perturbation magnitudes and file saving are the synthetic generator's own, so a label
means exactly what it means there; defects are injected into the layout (the
illustration placed so its real ink crosses the safe line by the planned depth, the
caption set at the planned size and contrast, the rule at the planned stroke).

`real_art_v2` was built with `--exclude real_art.jsonl`: zero illustrations in common
with the set the fixes were diagnosed on.

## 3. What real artwork broke, in order

| Step | real_art cv_decider | Cause found | Fix |
|---|---|---|---|
| synthetic-era engine | 56.7% / **7.8%**, 43% false-reject | 70 of 117 false rejects: **text detector found no text** on antialiased captions, and the no-text guard escalated | PaddleOCR **DBNet** via `rapidocr_onnxruntime` (model in the wheel, CPU, no download). Text found in 438/442 files vs 311. Detection only — text content never leaves the detector |
| + DBNet | 69.3% / 8.3% | pale captions never measured: each letter is below the element-size floor | contrast measured **per text line** against its local background, on the solid core of the glyphs (grey 222 on white: 11.5 measured, 11.5 true) |
| | | 22 of 26 artefactual thin-line flags were **antialiased text fringes** | text exclusion padded by 15% of line height |
| | | a 1.5 in hairline rule beside a dense illustration held **< 0.2% of the ink** and was skipped as speckle | components ≥ 0.08 in long are always measured |
| + contrast, fringes, floors | 75.2% / 6.5% | stroke mask threshold is relative to the darkest ink; with black outlines, a **mid-grey rule is invisible** | stroke mask never demands more than 24 levels of difference |
| | | a caption too pale for the detector hides behind **anything else the detector found** | second detection pass on a 4× difference-amplified copy |
| | | refit learned from label-flawed synthetic files that crossing the cut is fine | margin depth > 1.0 (past the cut line) is a **guard**, not a model input |
| + all of the above (dev) | 78.9% / 4.1% | — | freeze |
| **fresh real_art_v2** | **78.1% / 6.2%** | §5 | — |

The gap between 4.1% on the diagnosed set and 6.2% on unseen art is the cost of having
fixed against specific files. It is why the fresh set exists.

## 4. A vector oracle for labels real art does not come with

Labels by construction cover *injected* defects only. An illustration's own content is
unlabelled: a twemoji with a 0.12 pt highlight on a 2 pt-minimum banner is a real
thin-line problem whether or not the plan asked for one.

Because the art is vector, the pipeline can be refereed: re-render the identical layout
at 4× from the SVG and measure there. For all 45 THIN_LINES flags on "clean" files in
the first real set:

- **26 were artefacts** of rasterising at print resolution (true stroke ≥ the minimum) —
  engine errors, fixed above.
- **19 were genuine fine detail** the labels did not cover — the rule was right.

This is the method to keep: synthetic labels for injected defects, a high-resolution
vector render as ground truth for everything the design itself contains. It needs no
customer data and no human labelling.

## 5. What still fails on unseen real art

### real_art_v3 (sealed score 1.4%, 3 false approves), diagnosed after scoring

| Case | Label | Finding |
|---|---|---|
| case-00425 | aspect 1.1× (plus in-spec resolution and bleed) | **Label error.** The builder applied the 2× in-spec bleed, then stretched that canvas by 1.1× the tolerance. Against the canvas the spec requires, the file deviates 0.3× the tolerance: it is in spec, and approving it is correct. Fixed in both builders (`aspect_base_px_w`). |
| case-00400 | safe zone 2.0× | **Genuine miss: the text detector boxed the figure as a one-glyph line**, and text boxes were blanked before margin analysis. |
| case-00289 | safe zone 1.1× | **Genuine miss: a hand touching the caption's last letter shared its text box**, and was blanked with it. |

*Correction:* the first write-up of this table blamed dark-on-dark contrast and
anti-aliasing for the two genuine misses. Measuring with and without the text exclusion
showed both are the exclusion (depths 2.04 and 1.11 once it is removed).

Fixed afterwards and checked on spent sets only:
- The margin check now removes only a text box's own colours (ink, background, and the
  blend between them), not the whole box.
- The shifted synthetic misses (all depth 2.0, top/bottom) were a generator artefact: the
  mark was drawn in the border band's own colour and was invisible. With the mark
  visible, the detector still missed it, merging it into the band; `_band_embedded` now
  measures other-coloured elements inside a band.

| Spent set | before | after both fixes |
|---|---|---|
| real_art_v3 (label-fixed) | 79.6% / 0.9% | 78.9% / **0.0%** |
| shifted_v4 (rebuilt) | 85.4% / 1.4% | 85.0% / **0.0%** |
| shifted_v5 (rebuilt) | 81.7% / 1.5% | 81.7% / **0.0%** |
| holdout_v5 | 81.7% / 0.0% | 81.4% / 0.0% |
| real_art_v2 | 77.4% / 3.7% | 76.7% / 3.7% (caption-label errors, §5 below) |
| real_art (v1) | 80.4% / 3.1% | 80.0% / 3.1% (same) |

These are development numbers on sets already used. The claim waits for a sealed set.

### real_art_v2 (sealed score 6.2%, 14 false approves)

| Missed defect | Count | Most likely cause |
|---|---|---|
| LOW_CONTRAST | 7 | grey captions on coloured backgrounds were labelled by *intended* ΔE, not achieved; several were in spec (label error, fixed in the v3 builder: `caption_colour`) |
| THIN_LINES | 3 | stroke measurement on a luminance mask; fixed for free-standing strokes by the colour-region rule |
| CONTENT_IN_SAFE_ZONE | 3 | antialiased illustration edges fall under the foreground threshold at 1.1× |
| TEXT_TOO_SMALL | 1 | — |

With the caption labels corrected, the spent sets read 4.1% (v1) and 3.1% (v2).

## 6. What this is not

- **Not customer uploads.** Professional illustration is cleaner than what customers
  send: no JPEG artefacts, no screenshots, no phone photos of drawings, no mixed vector
  and raster. Every number here is still an upper bound for production.
- **No reviewer decisions.** The labels say what was injected, not what an artist at the
  company would have done. Real reviewer decisions on real files remain the thing that
  would change confidence most (limits.md §10).

## 7. Found on the way, not changed

- **The text-size rule compares ink height with a font size.** DBNet boxes measure
  0.69-0.77 of the planned point size — cap height, ~0.71 em. So all-caps text reads ~30%
  small against a minimum *font size*. That errs toward false rejects; dividing by 0.7
  blindly would risk false approves on mixed-case lines with descenders. Needs a
  per-line case/descender estimate, not a constant.
- **Two OpenCV builds are installed side by side** (`opencv-python` for RapidOCR,
  `opencv-python-headless` for features). Same version, so they coexist here; a
  production image should pick one.
- **The synthetic label flaw grew teeth.** DBNet correctly leaves a glyph clipped at the
  canvas edge out of the text line, so the generator's caption overrun (limits.md §11) now
  reads as an element past the cut. Correct in print terms; counted as false rejects
  against the labels.

## 8. Reproduce

```bash
pip install -e ".[dev,data]"
python -m capstone.data.fetch_art
python -m capstone.data.real_art -n 450 --seed 20260924                       # diagnosed
python -m capstone.data.real_art -n 450 --seed 20260930 --out real_art_v2 \
    --exclude real_art.jsonl                                                  # validation
python -m capstone.evals.harness cv_decider --manifest capstone/data/real_art_v2.jsonl --split all
```
