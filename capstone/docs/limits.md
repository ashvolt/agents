# Documented limits

Constitution Principle VII: documented limits are a deliverable, not an appendix. This
file says what the system cannot do and where it is known to fail. Everything here was
found by measurement or is a known property of the design — none of it is hypothetical
hedging.

**Last updated:** 2026-09-23, after the first agent sweeps.

> **Unofficial project.** No affiliation with Sticker Mule. All data synthetic.

---

## 1. The biggest one: this is graded against art I generated

The dataset and the checks share my assumptions about what each defect *is*. Straddling
thresholds (0.5x / 0.9x / 1.1x / 2.0x) catches boundary bugs, unit-conversion errors and
inclusive/exclusive mistakes — it caught four real ones — but it cannot catch an
assumption that is wrong in both the generator and the checker simultaneously.

Concretely, generated art is far simpler than real customer uploads. Real files carry
embedded ICC profiles, layer structures, vector/raster mixes, spot colours, overprint
settings, and fonts that do not render as expected. None of that exists here.

**What this means for the numbers:** treat every score in this repo as an upper bound.

## 2. Measured gaps

From the `rules_only` baseline on 159 train cases:

| Check | Recall | Why it is not 100% |
|---|---|---|
| `CONTENT_IN_SAFE_ZONE` | 0% | Not computable. This is bucket 3 and needs the model. |
| `UNINTENDED_TRANSPARENCY` | 60% | Only fires when the product disallows alpha; cases on transparency-permitting products are correctly skipped but count against recall in this table. |
| `TEXT_TOO_SMALL` | 67% | Detector limits — see §3. |
| `THIN_LINES` | 93% | Rasterisation limit — see §4. |
| `ASPECT_MISMATCH` | 94% | Suppressed when bleed is missing — see §5. |

`rules_only` scores **84.6% auto-approve with a 23.6% false-approve rate**. That breaches
SC-002 by roughly 23x, and 13 of the 17 wrongly approved files are safe-zone cases. Pure
Python is not shippable for this problem, and that is the measured argument for the
vision pass rather than an assumed one.

## 3. The text detector misses text

`ConnectedComponentDetector` groups connected components into horizontal lines. It fails
on:

- Touching or heavily kerned glyphs, which merge into one component.
- Lines shorter than three glyphs, which are discarded as artwork.
- Text on a busy background, which merges with the background structure.
- Rotated or curved text, because grouping assumes a horizontal baseline.

**Every one of those fails toward missing text**, which is the dangerous direction: an
undetected small-text defect becomes a false approve. The tool payload tells the model
explicitly that `text_lines_found: 0` means the detector found none, not that the file
contains none — but a model that ignores that caveat will approve a file it should not.

Choosing a production detector is OQ-3 and is decided by measured recall at small point
sizes, not by popularity.

## 4. Stroke width has a quantisation band, and it causes false rejects

Measured 2026-09-23. At 300 DPI one pixel is **0.24 pt**, and the 0.5 pt minimum stroke is
2.08 px. Every nominal width from **0.40 to 0.60 pt rounds to 2 px**, which measures back
as 0.48 pt:

| nominal | drawn | measures | flagged |
|---|---|---|---|
| 0.45 pt | 2 px | 0.48 pt | yes — correct |
| 0.50 pt | 2 px | 0.48 pt | yes — **false reject** |
| 0.55 pt | 2 px | 0.48 pt | yes — **false reject** |
| 0.60 pt | 2 px | 0.48 pt | yes — **false reject** |
| 0.65 pt | 3 px | 0.72 pt | no |

So **a legitimate 0.55 pt stroke is reported as too thin.** The file really does contain a
2 px stroke; the check cannot resolve finer than one pixel.

This is not fixed by loosening the threshold. Loosening it to absorb the band would let
genuine 0.40 pt defects through, and Principle I says take the recoverable error: a false
reject costs the customer one round trip, a false approve costs a misprint.

**The band widens as resolution falls.** At 150 DPI one pixel is 0.48 pt, the 0.5 pt
minimum is 1.04 px, and almost every thin stroke lands inside it — so on low-DPI products
this check is close to useless in both directions. `vinyl-banner` (72 DPI minimum,
2.0 pt strokes) is the worst case in the current spec table.

Related: a defect at 1.1x past the limit and one at 2.0x past it can produce identical
pixels, so per-magnitude recall flattens at the bottom end and the eval cannot distinguish
severity there.

## 5. Bleed and proportion are entangled

Without a trim box in the file, a file short on bleed necessarily has a different canvas
ratio, purely because of the missing margin. Reporting that as a proportion defect tells
the customer to fix something that is not wrong — the second-ranked failure mode in
brief.md §6.

The system therefore **suppresses `ASPECT_MISMATCH` whenever `MISSING_BLEED` fires**. A
file with both defects reports only the bleed problem, and the aspect problem surfaces on
resubmission. Real preflight tools behave the same way, but it does mean a two-defect
file produces a one-defect fix request.

## 6. The cost target passes for the wrong reason

Measured 2026-09-23 on Haiku 4.5:

| Loop shape | cost/file | 159-case sweep |
|---|---|---|
| 2 model calls | $0.0073 | ~$1.16 |
| 4 model calls | $0.0146 | ~$2.31 |

SC-003 ($0.01/file) **passes at 2 calls and fails at 4**. Turn count decides it.

**Why that is not a clean win.** The mean image in this dataset costs ~452 tokens, because
a 2x2 in sticker at 150 DPI is 338 px. Real customer uploads are typically 2,000-4,000 px,
hit the 1,100 px downscale cap, and cost roughly 1,600 tokens — about 3.5x more. At the
4-call shape on realistic artwork the cost lands near $0.018/file and breaches the target.

So: **the cost target is met by the test data, not demonstrated by the design.** Treat
SC-003 as unproven for production until it is measured on real-sized files. Batch (-50%)
is the unused lever if it turns out to be needed.

**Prompt caching does nothing on this build.** The cacheable prefix is 1,696 tokens and
Haiku's minimum is 2,048, so the `cache_control` breakpoint is a silent no-op. A sweep
reporting `cache hit rate: 0%` is expected here, and `SweepReport.cache_suspect` will fire
incorrectly. Caching becomes real when a longer system prompt and larger artwork push the
prefix past the minimum naturally; padding it to get there would be cargo cult.

## 7. SC-002 is not measurable on this dataset

**The most important limitation in this document.** Added 2026-09-23.

SC-002 requires a false-approve rate at or below 1%. The train split has 65 clean cases.
At a ~70% approve rate that is about 45 approvals, so:

| clean cases | ~approvals | one wrong approval scores | can 1% be observed? |
|---|---|---|---|
| 22 (a 50-case run) | 15 | 6.7% | no |
| 65 (full train) | 46 | 2.2% | no |
| 200 | 140 | 0.7% | yes |
| 450 | 315 | 0.3% | yes |

**The rate cannot land on 1%.** It is 0%, or it is at least 2.2%. Every false-approve
figure quoted in this repo is quantised in steps of roughly 2-7% depending on the run, and
"passes SC-002" can only ever mean "zero wrong approvals were observed" — which, by the
rule of three, is consistent with a true rate as high as 3/45 ≈ 6.7%.

Distinguishing 1% from 0% with any confidence needs ~300 approvals, so ~430 clean cases,
so roughly **1,100 total cases** at the current 40% clean mix.

Two honest options, neither yet taken:

1. **Grow the eval set to ~1,100 cases.** Free to generate, but an agent sweep over it
   costs ~$13 at the measured $0.0117/file — more than the project's remaining budget.
2. **Restate SC-002 as what a 200-case set can actually support**, e.g. "zero false
   approves observed, 95% upper bound below X%", and report the bound rather than the
   point estimate.

The harness now reports the 95% upper bound, the resolution, and an explicit warning when
a run is too small for the constraint to be meaningful. That does not fix the problem; it
stops the number being read as more precise than it is.

## 8. Not yet measured at all

Honest status, not a roadmap:

- **The agent has never completed a sweep.** The API key in the working `.env` is
  invalid, so the only agent numbers in this repo are from the degradation path.
- **No held-out score exists.** The holdout split is sealed and will be read once, at the
  end (T110). Any number quoted before then is a train-split number.
- **Confidence threshold is a placeholder** (0.70). OQ-2 says fit it on the train split;
  it has not been fitted.
- **Prompt caching cannot engage at all** on this build — prefix below the minimum, §6.
- **The red-team suite does not exist.** Injection resistance is designed
  (`injection_suspected` forces escalation, image content is framed as untrusted data)
  but untested. SC-006 has no measurement behind it.
- **MCP server, CI gate, HITL queue, runbook alerts** — not built.

## 9. Design choices that are limits by intention

- **Not multi-agent.** One agent plus deterministic tools. If the evals ever show a single
  agent cannot meet SC-001/SC-002, that gets revisited with evidence.
- **No self-hosted vision model.** Costs more than Haiku below ~30K files/day, and
  calibration matters more than raw accuracy when escalation is threshold-based.
  Arithmetic in research.md D-2.
- **No database.** JSONL on disk. Fine at 200 cases; revisit when trace volume outgrows a
  file scan.
- **The product spec table is invented.** Plausible for the class of product, but not
  sourced from a real print operation. Every threshold in it is an assumption.

## 10. What would change my confidence most

In order:

1. A hundred real customer files with real reviewer decisions. Everything above is
   downstream of not having them.
2. A successful agent sweep, so the model's contribution is measured rather than argued.
3. The red-team pass, because injection via rendered image text is the attack this design
   invites and it is currently untested.

## 11. Label flaws found by the CV decider — added 2026-09-23

Building features that describe the cut margin exposed two places where the generator's
labels and its pixels disagree. Neither is fixed in the generator, because every dataset
and result in this repo depends on it; both are worked around and recorded.

- **Label text overruns the trim on small products and is labelled clean.** The generator
  draws its text line from the left of the safe area without constraining its width, so
  on small stickers the text runs into the keep-out margin and sometimes to the canvas
  edge. In print that is a defect. The margin measurement excludes detected text so a
  classifier does not learn "type at the blade is harmless" — which means **text at the
  blade is currently caught by nothing.**
- **Some safe-zone defects are pixel-identical to clean files.** A same-colour mark that
  sits entirely inside a full-width border band of equal thickness leaves no trace
  (case-00341 in shifted_v3: a 28 px mark inside a 28 px band). No decider can recover
  it. Real logos are rarely the exact colour of the border they touch, so this is mostly
  a generator artefact — but a logo that merges with a border *is* real, and is why the
  band-protrusion guard exists (decider.md §3).

## 12. The model-free pipeline does not meet SC-002 at scale — added 2026-09-23

rules_only scored 0.0% false approves on the 88-case holdout. On three fresh sets of
400-600 cases it scored 2.1-2.5%, breaching the 1% limit every time. §7 predicted this:
44 approvals cannot distinguish 0% from 6.8%. The CV decider (decider.md) passes on the
same sets at no cost; the deterministic-only recommendation in results.md §7 is
withdrawn.

## 13. Verified fixes are verified by the checks that found the problem — added 2026-09-23

The fix engine (scene-narration.md) re-runs the full preflight on every corrected proof.
That proves the checks pass, not that the proof is right. Two cases where it misled
before being tightened: a stretched file padded to the correct canvas passed every rule
with the design still distorted, and a proof with one unverified fix was approved by
the decider anyway. Both are now blocked, but the class of failure is structural: **any
defect the rules cannot see, a fix can hide.** The customer's proof approval is the real
check, and scaling a design — even to 97% — is only acceptable because it is a proof.

## 14. Real artwork does not yet meet SC-002 — added 2026-09-24

On 450 unseen real illustrations laid out as sticker uploads (real-art.md), the CV
decider scores 78.1% auto-approve with **6.2% false approves** (95% upper bound 9.4%),
against a 1% limit. Synthetic holdouts pass (0.0% on 600) and a shifted synthetic set
narrowly fails (1.4%). The remaining real-art misses are low-contrast captions (7), thin
lines (3), safe-zone intrusions at 1.1x (3) and one small-text case. **The synthetic
headline should not be quoted without this one next to it.**


## 15. Red-team gaps — added 2026-09-24

`python -m capstone.evals.redteam` builds 12 adversarial files and runs the shipped
pipeline on them. With DBNet installed: 0 failures, 2 known gaps, 1 case held for the
wrong reason. The two gaps are both false approvals.

- **RT05: a hairline embedded in thick ink.** A 0.24 pt line (minimum 0.5 pt) drawn
  across a filled, outlined shape is approved. On the brightness mask the line joins the
  shape and becomes one component whose median stroke is the thick part. On colour
  regions it joins the same-colour outline, and colour regions are only measured when
  free-standing: measuring every region dropped real-art approval to 32%. Closing this
  needs a thin-*branch* measure (long runs of thin pixels inside a thick component), and
  that has to be validated on fresh real art before it ships.
- **RT06: upscaled low-resolution art.** A 60 DPI file resampled to 300 DPI is approved.
  Resolution is read from pixel count and metadata. Nothing measures whether the pixels
  carry detail (high-frequency energy, or the repeated rows and columns of a
  nearest-neighbour upscale). In the first run this case was rejected, but by accident: it
  tripped the text-size check instead.
- **RT04 held for the wrong reason.** Text about 5 ΔE from its backing, inside a busy
  pattern, is escalated for text size. The contrast check does not fire.

The first run reported two more failures (RT03, RT04). They came from the fallback text
detector: that run used a Python without `rapidocr_onnxruntime`. The fallback now warns,
and the red-team report prints which detector ran.
