# AI-generated artwork — does the engine survive Stable Diffusion?

**Date:** 2026-09-25 · **API spend:** $0.00 · **Code:** `capstone/data/fetch_ai_art.py`,
`capstone/data/ai_art.py` · **Sealed set:** `ai_art_v1` (18d0756), scored once

> **Unofficial project.** Not affiliated with Sticker Mule. The images are from
> DiffusionDB (CC0), not customer uploads. The layout and the labels are ours.

---

## 1. Headline

| Sealed set | rules_only | cv_decider (shipped) |
|---|---|---|
| real_art_v5: 1,000 real illustrations (round 4, for comparison) | 73.9% / 3.9% FAIL | **78.7% / 0.0%** (0 of 500, UB 0.6%) |
| **ai_art_v1: 1,000 Stable Diffusion sticker images** | 51.6% / 4.2% FAIL | **54.3% / 0.0%** (0 of 339, UB 0.9%) **FAIL on SC-001** |

*Auto-approve / false-approve. SC-001 ≥ 60%, SC-002 ≤ 1%. UB is the exact one-sided
95% upper bound (Clopper-Pearson).*

**On AI art the shipped pipeline stays safe but stops being useful.** Out of 339
approvals, none was wrong, and the bound (0.9%) is under the limit. But 45.7% of the
clean files were not approved, against 21.3% on real illustration, and the approve rate
falls below the 60% target. Almost all of that gap is the images' own content: fine
detail and garbled small lettering that really do measure under the print minimums (§4).

The model-free rules breach SC-002 again (14 wrong approvals, 12 of them safe-zone
intrusions). The CV decider catches all of them. As on every earlier set, the rules
alone are not shippable.

## 2. The set

The images are Stable Diffusion outputs from DiffusionDB (poloclub/diffusiondb, CC0,
2 million images with the prompts people actually typed). The pool is every image whose
prompt asks for a sticker, decal, die-cut, logo, badge, emblem or patch, with both
published NSFW scores ≤ 0.1, one image per distinct prompt: 6,735 images in 1,682
archives. From that pool, 1,100 were drawn with seed 20261010. They were fetched with
HTTP range requests, so only the chosen zip members were read, not whole archives. The
fetched images live in a gitignored cache.

Each case uses real_art.py's layout and labels (same plans, perturbation magnitudes,
caption, rule, saving). Only the illustration is swapped:

- **Background.** If ≥ 75% of the image border is within 24 levels of its median colour,
  that colour is cut out wherever it touches the border, the way a background remover
  would do it (435 of 1,000). Otherwise the whole rectangle is the artwork (565).
- **Placement by visible ink.** Raster art is placed by the pixels that differ from the
  sticker background by more than 24 levels, not by its alpha box. So an injected
  safe-zone intrusion crosses the line with something that shows. In the builder
  preview, an uncut off-white margin had counted as the intrusion, which is why this
  rule exists.

Frozen at 71f7fd1 (engine unchanged since ac59d65, decider model md5 8567b545), sealed
at 18d0756, scored once:

```bash
python -m capstone.data.fetch_ai_art -n 1100 --seed 20261010
python -m capstone.data.ai_art -n 1000 --seed 20261011 --out ai_art_v1
python -m capstone.evals.harness cv_decider --manifest capstone/data/ai_art_v1.jsonl --split all --save
python -m capstone.evals.harness rules_only --manifest capstone/data/ai_art_v1.jsonl --split all --save
```

Runs: `20260925T075401Z-cv_decider`, `20260925T073622Z-rules_only` (gitignored, like
every run file).

### Environment check before scoring

A fresh install resolved `rapidocr_onnxruntime` 1.4.4. That version has no
`RapidOCR().text_detector`, so the detector failed to import. The 1.3 line swaps the
bundled PP-OCRv3 model for PP-OCRv4. The dependency is now pinned `>=1.2.3,<1.3`
(1.2.13 here, both OpenCV builds at 5.0.0.93). With that environment, both gates
reproduce their committed baselines exactly before anything was scored:

- synthetic: 312 cases, 167 of 167 approvals clean, the same per-issue recall;
- real_art_v3: 79.9% / 0.0%, rebuilt from scratch with a manifest identical to the
  committed one.

## 3. Per-issue recall (cv_decider)

| Code | Recall | Note |
|---|---|---|
| ASPECT_MISMATCH | 58/60 | |
| CONTENT_IN_SAFE_ZONE | 30/44 | the misses were escalated or rejected for something else; none was approved |
| LOW_CONTRAST | 60/60 | |
| LOW_RESOLUTION | 49/49 | |
| MISSING_BLEED | 54/54 | |
| TEXT_TOO_SMALL | 55/55 | |
| THIN_LINES | 63/66 | |
| UNINTENDED_TRANSPARENCY | 22/22 | |
| UNREADABLE_FILE | 15/15 | |

## 4. Why clean AI files are not approved (diagnosed after scoring)

285 of 624 clean files were not approved: 246 sent back for a fix, 39 escalated. The
blocking codes on those files:

| Code on a clean file | Files | Sole blocking code |
|---|---|---|
| THIN_LINES | 181 | 145 |
| TEXT_TOO_SMALL | 93 | 57 |
| ASPECT_MISMATCH | 7 | 4 |
| LOW_CONTRAST | 3 | 2 |

### THIN_LINES (181)

The check reports no location, so a copy of its two passes that also records the
winning component's box was run over all 181 files (read-only; the engine was not
changed). Almost every measurement is one or two pixels at the print DPI: 0.24 pt at
300 dpi, 0.48 pt at 150, 1.0 pt on 72 dpi banners.

- **13 rule-shaped (≥ 100 px wide, ≤ 2 px tall). 11 are the layout's own rule on a
  file labelled "THIN_LINES 0.9×"** (inside spec). But min/0.9 = 0.556 pt is 1.16 px at
  150 dpi, which rounds to 1 px, or 0.48 pt, *under* the limit (checked on the pixels of
  case-00377: one row at 132 on white). The other 2 were not inspected. The engine measured what was drawn;
  the label is wrong. This is limits.md §4 (the quantisation band) showing up as a
  label error. It is not specific to AI art.
- **The rest, on a sample of 20 located and inspected by eye:**
  - **14 genuine fine detail in the image:** hairlines, AI speckle noise, texture in a
    512 px image stretched to print size. It really is under the minimum. Nobody
    labelled it, because the image's own content is unlabelled (as in real-art.md §4).
    Unlike the vector libraries, there is no high-resolution original to referee
    against.
  - **5 cut-out fringe:** the 1 px antialiased halo or stray residue that `cut_out`
    leaves where the background met the art. This is a builder artefact, though a
    realistic one: real background removers leave the same halo. It is why cut-out
    files are approved less often (48.5%) than whole rectangles (58.8%).
  - **1 sliver created by the text exclusion:** the padded text box covers most of an
    image that overlaps the caption, and the uncovered edge measures 1 px. Across all
    181, the thinnest component touches the exclusion mask in 18.

### TEXT_TOO_SMALL (93)

On a sample of 8 sole-code files, **5 were genuine**: Stable Diffusion's garbled small
lettering (the banner on a badge, the text around a roulette logo, "Windows 2003" under
a logo) really is below the minimum at the ordered size. **3 were DBNet boxing image
detail as text** (helmet stripes, a webcam icon, a sheriff's badge).

### So what is the right approve rate?

A file carrying 2-pixel speckle noise or 2 pt garbled lettering is not print-ready by
the spec's own rules, whatever its label says. Most of this "false reject" rate is the
engine being right about content the labels do not cover. The honest summary is:
**on AI art, roughly one clean file in four carries real sub-minimum detail of its
own, and the engine sends it back** (an estimate scaled up from the two samples, not a
measurement). Whether it *should* is a product question: a speck that
disappears on press does not hurt anybody. That question belongs to the spec, not to a
threshold tweak.

## 5. FAKE_TRANSPARENCY is not ready (found on the raw images)

The painted-checkerboard check (ac59d65) was built for exactly this kind of image but had
never seen one. It fired **0 times** in the scored run, because inside the layout the
image covers less than the check's 10% floor. Run directly over the 1,100 raw DiffusionDB
images, the files a customer would upload as-is:

- 10 prompts mention png, transparent or no background;
- **16 images flagged, and none of the 16 is a checkerboard.** They are smooth grey
  vignette or paper backgrounds behind a logo. **1.5% of raw AI uploads would be
  rejected with a false message.**

The cause is multiple comparisons. The search tries about 60 cell sizes times up to 16
phases, and at large cells it accepts as few as 16 known samples. A smooth two-level
grey field clears 85% parity agreement somewhere by chance. Every false hit sits at
38–60 px cells with 0.85–0.94 agreement; a painted grid alternates at near 1.0 over many
cells. No real checkerboard turned up among the 1,100 images.

It was not fixed here. A fix has to be validated on AI images it was not diagnosed on,
and these 16 are now spent. The live-generation card in the demo (`/api/generate`)
checks a fresh image as an upload, so this false reject can happen on stage.

## 6. What this does not show

- **Not customer uploads.** These are real AI images, but the layout, caption, rule and
  labels are ours, as in real-art.md.
- **2022 Stable Diffusion, 512 px.** Current generators (FLUX, which the demo uses)
  produce cleaner edges and better lettering. The demo's generator has still not been run
  against a live provider.
- **The fine-detail findings are unrefereed.** With no vector original there is no 4×
  ground truth. The 14 / 5 / 1 split above comes from eyeballing 20 files, not from a
  measurement.

## 7. Next

1. **FAKE_TRANSPARENCY:** require a minimum number of sampled cells (not 16) and near-
   perfect alternation, or verify sharp edges at the cell boundaries. Validate on a fresh
   draw from the 5,635 unfetched images in the pool, plus real painted checkerboards.
2. **The 0.9× stroke label:** round the drawn stroke *up* for within-spec plans, or
   record the drawn width in the label (as LOW_CONTRAST already does). This is a builder
   fix and needs no engine change.
3. **Cut-out fringe:** decontaminate the halo (a 1 px erode of the cut) in the next
   builder version, behind a flag so that ai_art_v1 still rebuilds byte-for-byte.
4. **The product question in §4:** whether sub-minimum *detail* (as opposed to lines
   and type the customer designed) should block. That is a spec change, and it needs a
   printer's answer, not ours.
