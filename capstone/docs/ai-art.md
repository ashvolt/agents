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
| **ai_art_v2: 1,000 unseen images, fixed builder (§8)** | 55.6% / 1.4% FAIL | **57.1% / 0.0%** (0 of 354, UB 0.8%) **FAIL on SC-001** |

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

## 5. FAKE_TRANSPARENCY: false alarms fixed, real AI checkerboards still missed

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

### 5a. The fix, and a validation on unseen images (51f2f35)

The cause was narrower than multiple comparisons alone. On a textured grey backdrop, the
two "greys" the check picked were 10 levels apart *inside one noisy distribution*
(image 2382746d: 155 and 165 on paper whose luma runs from 130 to 170). The rule was
changed, and committed with its validation plan **before any validation image was
fetched**. All three conditions must now hold:

- the luma histogram dips between the two greys (the valley is at most half the lower
  peak): painted greys are two peaks, paper texture is one hump;
- at least 64 sampled cells (an 8 × 8 grid), not 16;
- at least 95% of those cells alternate, not 85%.

Every change is stricter, so no file can newly fire; the only risk is lost true
positives. The new test is 20 synthetic textured backdrops; the old rule fires on 6 of
them.

The validation was run once:

| Set (never seen before; spent prompts excluded) | Old rule | New rule |
|---|---|---|
| 1,000 sticker-prompt images, seed 20261012 | 8 flagged, **all false** | **0** (95% bound 0.3%) |
| 400 images whose prompt asks for transparency, seed 20261013: 3 light painted checkerboards, 2 black-and-white ones, labelled by eye before either rule ran | 0 of 3, 0 of 2; 3 false flags | 0 of 3, 0 of 2; 0 false flags |

**The fix removes the false alarms: 0 of 1,000, against 8 for the old rule (and 16 of
1,100 on the first set). But neither rule catches a real Stable Diffusion
checkerboard.** Diagnosed on the three light ones, which are now spent:

- Their levels are fine (253 and 168 on one, 253 and 238 on another).
- The grid is what fails. The best fixed lattice fits only 70–83% of cells, and only at
  62–63 px cells with about 30 sampled cells, while the visible cells are about 13 px.
  Stable Diffusion paints an *impression* of a checkerboard: cell size drifts and rows
  bend. One period and one phase across the whole image never line up with that.
- One of the three has mid-grey (75) dark cells, below the check's lightness floor.

The check's only positives were ever perfectly regular synthetic grids. It still makes
sense for a checkerboard pasted in from a real editor, which is regular, but as shipped
**it does not detect the AI case it was written for.** Detecting that needs a *local*
alternation test (small windows, each with its own period and phase), and more than 3
examples to validate it. At about 0.75% of transparency prompts, 3 examples is what 400
images yield, so a real positive set means fetching several thousand, or using the 14M
DiffusionDB-large.

The tightened rule is kept. It is strictly safer, and a check that fires on nothing real
does no harm as long as nothing claims otherwise. The demo must not present
FAKE_TRANSPARENCY as catching AI checkerboards.

## 6. What this does not show

- **Not customer uploads.** These are real AI images, but the layout, caption, rule and
  labels are ours, as in real-art.md.
- **2022 Stable Diffusion, 512 px.** Current generators (FLUX, which the demo uses)
  produce cleaner edges and better lettering. §10 is a first live look at FLUX: 19
  images, unlabelled.
- **The fine-detail findings are unrefereed.** With no vector original there is no 4×
  ground truth. The 14 / 5 / 1 split above comes from eyeballing 20 files, not from a
  measurement.

## 8. ai_art_v2: the builder fixes, measured

Sealed at a61d2ed (frozen at 1777f80; engine last changed at 51f2f35), 1,000 images
that no earlier set, validation or diagnosis had seen, built with `--clean-edges
--drawn-stroke-labels`, scored once (runs `20260925T113546Z-cv_decider`,
`20260925T111800Z-rules_only`):

- **cv_decider 57.1% / 0 of 354 wrong approvals (exact bound 0.8%).** Safe again, and
  still under the 60% target.
- rules_only 55.6% / 1.4% (5 wrong, all safe-zone intrusions). It breaches, as always.
- THIN_LINES recall 67/67. CONTENT_IN_SAFE_ZONE 39/56: none of the misses was approved.

v2 draws different images from v1, so on its own it cannot say how much of the change is
the builder. **A same-image A/B does:** the v1 images rebuilt with both flags on, scored
with cv_decider (run `20260925T121827Z-cv_decider`, a diagnostic, not a sealed claim):

| v1 images, cv_decider | Auto-approve | Wrong approvals | Cut-out clean files blocked | Whole-rectangle clean files blocked | THIN_LINES on clean files |
|---|---|---|---|---|---|
| flags off (as sealed) | 54.3% | 0 of 339 | 140/272 (51%) | 145/352 (41%) | 181 |
| flags on | 57.7% | 0 of 356 | 118/267 (44%) | 143/350 (41%) | 146 |

The fixes are worth about 3.4 points. The cut-out penalty is mostly gone, and 7 files
move from clean to defective because their rule is really drawn under the limit. **What
is left is the images' own content:** THIN_LINES still blocks 146 clean files and
TEXT_TOO_SMALL 86, on detail and lettering Stable Diffusion drew itself. No builder
change removes that, and no threshold should be tuned to hide it. Crossing 60% on AI art
is the product question in §4, not an engineering one.

## 9. Pricing the spec option: "detail shorter than L is not a stroke"

Priced with the code that would ship (`bucket2_pixels.STROKE_MIN_DETAIL_IN`; 0 = off, and
off is what ships) by `python -m capstone.evals.price_detail_rule`. This is a diagnostic
on spent sets, not a sealed score. real_art_v5 and mistakes_v2 were rebuilt here, and
their manifests are identical to the sealed ones. At L = 0 every set reproduces its
published score exactly.

| Set, cv_decider | L = 0 (ships) | 0.02 in | 0.04 in | 0.08 in (2 mm) |
|---|---|---|---|---|
| ai_art_v1 | 54.3% / 0 of 339 | 54.3% | 55.0% (+4 files) | 58.8% (+28) |
| ai_art_v2 | 57.1% / 0 of 354 | 57.1% | 57.6% (+3) | **59.2%** (+13) |
| real_art_v5 | 78.7% / 0 of 500 | 78.7% | 79.2% (+3) | 79.5% (+5) |
| mistakes_v2 | 87.0% / 0 of 240 | 87.0% | 87.0% | 88.0% (+3) |
| synthetic gate | 84.8% / 0 of 167 | 84.8% | 84.8% | 84.8% |

Wrong approvals by label: **0 at every length on every set**, and the red team is
unchanged (0 failed, the same 2 known gaps). Two things to read correctly:

- **Even the loosest rule does not reach 60% on AI art** (59.2% on v2). Short detail is
  only part of it. Longer thin detail and TEXT_TOO_SMALL (86 clean files blocked on
  v2, garbled lettering) are the rest.
- **"0 wrong" means nothing is wrong by label, not that nothing is wrong.** Labels cover
  injected defects, so every newly approved file (13 to 28 per AI set) carries real
  sub-minimum detail shorter than L that will print. That is the spec risk, and it is a
  printer's call.

**The decision this leaves.** Either:
- (a) quote AI art honestly at ~57%, safe, and let the reviewer queue take the rest;
- (b) adopt L = 0.08 in for about +2 points if a printer confirms specks under 2 mm are
  acceptable, then seal a fresh set;
- (c) accept that the remaining gap is the semantic question "is this lettering real
  text or texture", which measurement cannot answer and which would need a model, as
  advice to the reviewer only.

Nothing is adopted here. The constant stays at 0.

## 10. Live generation in the demo: FLUX.1-schnell, measured 2026-09-26

The demo's generate card had never run. It does now, with the environment's Cloudflare
keys (`generate.provider()` reports `cloudflare`), through the demo's own
`POST /api/generate`: 20 prompts, each checked as a 3 × 3 in die-cut sticker. These are
observations, not a scored set. Generated images have no labels.

- **19 of 20 generated,** in 1.5–4.2 s each, as 1024 × 1024 RGB JPEGs with no DPI (341 dpi
  effective at 3 in). All 19 were checked; none crashed.
- **Verdicts:** 3 approved, 4 escalated (all 4 the no-text guard: the detector found no
  text, so a person confirms), 12 sent back.
- **Why sent back:** THIN_LINES on 8 (nearly all 0.46 pt, which is 2 px of hairline
  detail at this size), LOW_CONTRAST on 3 (one a faint drop shadow FLUX paints under
  the sticker, 243 on 253), TEXT_TOO_SMALL on 3 (e.g. a 2.5 pt tagline under a bakery
  logo). The same pattern as DiffusionDB: generated fine detail measures under the
  minimums.
- **No painted checkerboards.** 5 prompts asking for a transparent background, run
  twice, gave 10 images on plain white, cream or colour backgrounds. FAKE_TRANSPARENCY
  stayed silent, correctly. The checkerboard problem is an older-generator one.
- **The lettering is still garbled** ("SKATE BOARD", "Slakeboard" for "skateboard").
- **One false refusal:** "sticker of a pizza slice with the text PIZZA TIME" is refused
  by Cloudflare's safety filter as NSFW, every time.

Two bugs were fixed. The page used to show the provider's raw error on a refusal; it now
says the safety filter refused the prompt and suggests rewording (`_explain`). And a
`seed` made every Cloudflare call fail (error 5006: the model does not accept the
field), so it is no longer sent. The demo never passed one, but any caller that did
got nothing. Both fixes are tested without network, and both were checked live.

## 7. Next

1. ~~FAKE_TRANSPARENCY false alarms~~: fixed and validated, 51f2f35 (§5a). **Still open:**
   a local alternation test that catches Stable Diffusion's irregular checkerboards,
   validated on a positive set of dozens, not 3.
2. ~~The 0.9× stroke label~~ and 3. ~~the cut-out fringe~~: builder flags
   `--drawn-stroke-labels` and `--clean-edges`, 53942b9. Both are off by default, so
   ai_art_v1 rebuilds identically. They are used from ai_art_v2 on.
4. ~~ai_art_v2~~: sealed and scored, §8. 57.1% / 0 of 354; the builder fixes are worth
   about 3.4 points on the same images.
5. **The product question in §4:** priced in §9. The stroke side alone buys at most
   about +2 points (59.2% at 2 mm, still under 60%). Decision (a), (b) or (c) in §9 is
   the owner's, with a printer's answer on specks.
