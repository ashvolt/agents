# Measure → Describe → Verify → Narrate

**How a small text model can explain an image, and suggest fixes, without seeing it.**

**Date:** 2026-09-23 · **Status:** spike, measured offline · **API spend:** $0.00 ·
**Code:** `capstone/tools/scene.py`, `capstone/tools/fixes.py`, `capstone/src/narrate.py`

> **Unofficial project.** Not affiliated with Sticker Mule. All artwork is synthetic.
> Every number is an upper bound on real uploads (limits.md §1).

---

## 1. The question

The decider ([decider.md](decider.md)) approves or escalates using five numbers, with no
model. The ask was the next step: send engine-derived data to a small model so it can
give context about the image and suggest fixes, **without relying on the model for
vision**.

The obvious version — describe the image in text, ask a model what it thinks — has two
known failure modes, and the design below exists to close both:

1. **A text model can only reason about what the description contains**, and nobody
   knows what the description dropped. Structured scene representations help LLM
   reasoning, but hallucination and lossy descriptions are the open problem
   ([Scene Graph Thinking, ICML 2026](https://icml.cc/virtual/2026/poster/62346);
   [schema-guided scene-graph reasoning](https://arxiv.org/pdf/2502.03450)). LLMs are
   notably weak on low-level geometry formats such as raw SVG
   ([VGBench](https://arxiv.org/abs/2407.10972)).
2. **A model that produces numbers will sometimes produce wrong ones**, and a wrong
   number in a customer message is the second-ranked failure mode in brief.md §6.

## 2. The pipeline

```
upload
  │
  ├─ MEASURE    rules + OpenCV (buckets 1-2, decider features)            exact, $0
  │
  ├─ DESCRIBE   scene document: every element's shape, colour, position   scene.py
  │             in inches from the trim, clearance to the safe line
  │     └─ VERIFY the description: redraw the artwork from the text
  │               alone, compare to the real pixels  →  fidelity score
  │
  ├─ FIX        applied fixes on a proof image (bleed, safe zone, hairlines,  fixes.py
  │             colour mode) + exact suggestions for the rest
  │     └─ VERIFY the fixes: re-run the whole preflight on the proof
  │               → proof_ready only if every fix verified, nothing new broke
  │
  └─ NARRATE    small text model writes the customer message and reviewer   narrate.py
                note from the scene + fixes
        └─ VERIFY the narration: every id and every number must exist in
                  the engine's output, or the deterministic template ships
```

Each step that could be wrong is followed by a check that runs on the step's own output,
not on trust. The model sits at the very end, does the one thing it is best at
(language), and cannot introduce a fact.

### What is new here

Individually these are known techniques. The combination for print preflight is the
contribution:

| Technique | Where it comes from | What it does here |
|---|---|---|
| Image → structured scene for an LLM | scene-graph reasoning literature | Scene document in trim-relative inches, designed for the question being asked |
| **Render-and-compare** | inverse graphics ([VIGA](https://arxiv.org/pdf/2601.11109), [IR3D-Bench](https://arxiv.org/html/2506.23329v1)) | **Measures how complete the description is** before any model reads it. Below 0.9 (`FIDELITY_FLOOR`) no model is called and the template ships. |
| Auto-fix preflight | PitStop / pdfToolbox ([Enfocus](https://www.enfocus.com/en/pitstop-pro)) — rule-based, mirror-to-bleed | Same class of fixes, plus **re-verification by the full pipeline** and a refusal to apply what would be dishonest (upscaling, re-colouring) |
| Grounded generation | general practice | **Numeric claim-checking**: every number the model writes must be an engine value at the precision written, or the narration is discarded |

The render-and-compare gate is the answer to failure mode 1: instead of hoping the text
captured the image, the pipeline redraws the image from the text and measures the gap.
Inverse-graphics work uses this loop to make *models* reconstruct scenes; here it
certifies a *deterministic* description before a model is allowed near it.

## 3. Measured results

Train split for development; **shifted_v3 is the untouched set** for the fix engine
(holdout_v3 was used to find two bugs, below, so its fix numbers are reported but are
not clean).

### Describe

| | shifted_v3 (278 files) |
|---|---|
| redraw fidelity (ink IoU, text excluded) | median **0.975**, min 0.931; 274/278 ≥ 0.95 |
| elements per file | median 7, max 31 |
| scene size | ~755 tokens median (estimate: chars / 3.5) |
| time | 0.17 s/file |

The scene does not grow with resolution. A real 2,000-4,000 px upload costs ~1,600
image tokens; its scene costs the same ~750 as a 400 px file.

### Fix and verify

| | holdout_v3 (282 not approved) | **shifted_v3 (184 not approved)** |
|---|---|---|
| applied fixes verified | 269/273 | **134/134** |
| — thin lines / bleed / colour / transparency | 118/118 · 38/38 · 31/31 · 12/12 | 59/59 · 22/22 · 15/15 · 5/5 |
| — design fit inside the safe line | 70/74 | 33/33 |
| **print-ready proofs, zero humans** | **88 (31%)** | **44 (24%)** |
| proofs hiding an unaddressed injected defect | 0 * | 0 * |
| suggestions with exact targets | 199 | 138 |

\* 7 and 2 files carry a transparency label on a product that *permits* transparency — a
known rule gap (limits.md §2). The CMYK conversion flattened the alpha in each.

### Narrate

| | |
|---|---|
| template narrations passing the claim check | **306/306** (no false positives) |
| fabricated numbers / ids caught | every case in the tests, incl. a fake model client |
| model input | ~820 tokens median incl. fixes |
| live model run | **not run — see §5** |

## 4. What went wrong on the way (kept deliberately)

1. **Ellipse-fitting misdescribed merged shapes** (fidelity 0.82 on a clean file). Two
   stripes touching an outline merged into one component and were redrawn as a full
   ellipse. Fixed with polygon rings (the SVG-path equivalent): median fidelity 0.955 →
   0.975. Render-and-compare found this; nothing else would have.
2. **Per-element moves verified 40 of 158.** Elements wider than the safe area cannot be
   moved clear, and undetected text arrives as glyph blobs that per-element moves would
   scatter. Replaced with fitting the whole layout, as a prepress artist does, capped at
   a 15% shrink before it becomes a suggestion.
3. **"Verified" was vacuous for the safe zone.** The safe-zone rule is advisory, so
   "not in the blocking set" passed every move. Now re-measured from a fresh scene of
   the proof.
4. **Thickened hairlines were left behind as slivers** when the fit moved their original
   boxes. Found because 13 fixes failed re-verification.
5. **A stretched file was "fixed" by padding it to the right canvas** (holdout_v3,
   case-00521). Every rule passed; the design inside stayed stretched. Bleed is now
   padded only when it is missing equally on every side.
6. **An unverified fix still reached "approve"** (case-00593): the decider accepted the
   proof although the fit had not verified. Now `proof_ready` requires every applied fix
   to verify — one failure sends the file to a person.

Items 5 and 6 are the important ones: **verification is only as good as the checks it
re-runs.** Both were fixed by tightening what counts as verified, not by loosening a
threshold.

## 5. Blockers — where I need you

| Blocker | Why it matters | What unblocks it |
|---|---|---|
| **No API key in this environment** | The narration model has never run. Everything above is measured without it. | A key in `.env` (never committed). Then `pytest -m integration` runs the real-model claim-check test, and a ~$0.50 sweep measures how often Haiku's narration passes the check vs. falls back. |
| **Hugging Face is blocked by the network policy** | Rules out trying a small *local* model (e.g. a 0.5-3B instruct model on CPU) as the narrator. | Allow `huggingface.co` in the environment's network settings, or accept Haiku/Jev only. |
| **No Jev access** | Jev returns decisions, not prose, so it cannot narrate — but it could replace the decider (decider.md §7). | Early-access key. |
| **Real artwork** | Every fidelity number assumes flat-colour art. Photos and gradients will score low — correctly routed to a human, but the share of real uploads that decompose cleanly is unknown. | A sample of real files. Unavailable under the integrity rules (PLAN.md §2). |

**Estimated narration cost if unblocked** (assumption, not measured): ~1,100 input +
~300 output tokens on Haiku 4.5 ≈ **$0.0026 per narrated file**. Only files that are
not auto-approved need one (~47% of the synthetic mix), so ~$5/day at 4,000 files/day,
against ~$26-70/day for the vision call it replaces.

## 6. Two properties that come free

- **Prompt injection via artwork text has no carrier.** The scene holds a text line's
  position and point size, never its content. limits.md §8 calls injection through
  rendered text "the attack this design invites"; on this path the model never receives
  the text. (If OCR is ever added for spell-checking, this property ends and needs its
  own defence.)
- **The model is optional.** The template narrator passes the claim check by
  construction and is what ships when the model is down, slow, over budget, or wrong.
  Degradation is a quality change in wording, never a change in facts.

## 7. What this does not show

- **The proof is right by the checks, not by a person.** Fixes are verified by the same
  rules that found the problem. §4 items 5-6 show how that can mislead; the customer's
  proof approval, which the business flow already has, remains the final check.
- **Scaling the design is a design decision**, even at 97%. It is applied only because
  the output is a proof the customer approves, never a silent change to what prints.
- **Text content is invisible** to every step: no spell-check, no "is this the right
  phone number". That is the price of the injection property above.
- **Fidelity measures ink placement, not meaning.** A redraw can match every pixel and
  the scene still cannot say "this is a logo".

## 8. Reproduce

```bash
pytest capstone/tests/test_scene_fixes_narrate.py          # offline, includes a fake model
pytest -m integration capstone/tests/test_scene_fixes_narrate.py   # needs a key
```
