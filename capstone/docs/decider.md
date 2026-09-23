# The CV decider — preflight without a vision model

**Date:** 2026-09-23 · **API spend:** $0.00 · **Code:** `capstone/tools/features.py`,
`capstone/src/deciders.py`, `capstone/evals/train_decider.py`

> **Unofficial project.** Not affiliated with Sticker Mule. All artwork is synthetic and
> generated locally. Every number here is an upper bound on real uploads (limits.md §1).

---

## 1. Headline

The Claude vision call is gone. OpenCV measures the image, a five-feature logistic
regression decides, and guards escalate what no measurement can settle. Two fresh
datasets, generated after the model was frozen and scored once each:

| | rules_only | **cv_decider** |
|---|---|---|
| **holdout_v3** — 600 cases, same distribution as train | 78.9% / 2.4% **FAIL** | **84.4% / 0.0% PASS** |
| **shifted_v3** — 400 cases, intrusions on any edge | 79.6% / 2.1% **FAIL** | **84.6% / 0.5% PASS** |
| escalation rate (holdout_v3 / shifted_v3) | 13.5% / 13.0% | **11.3% / 10.8%** |
| cost per file | $0 | **$0** |

*Auto-approve / false-approve. SC-001 is ≥60% approve; SC-002 is ≤1% false approve.
holdout_v3 has 304 approvals, so its 95% upper bound is 1.0% — the first run in this repo
large enough to actually resolve the 1% limit.*

**Against Claude**, on the only set both have been scored on (the original 88-case
holdout):

| | rules_only | agent_fast (Claude vision) | cv_decider |
|---|---|---|---|
| auto-approve | 82.0% | 82.0% | **88.0%** |
| false-approve | 0.0% | 0.0% | 0.0% |
| escalation | 17.0% | **13.6%** | **13.6%** |
| cost per file | $0 | $0.0066 | **$0** |

Claude's measured contribution was cutting escalations from 17.0% to 13.6%. The decider
reaches the same 13.6% on the same files, at no cost, and approves six points more. That
was the bar agreed before building (PLAN.md §11). The caveat from results.md §2 still
applies: at 44 approvals this comparison is directional, not significant.

## 2. Second finding: the model-free pipeline did not actually pass

results.md §1 reported rules_only passing both criteria on the 88-case holdout. At 600
cases it breaches SC-002 on every fresh set: 2.5% (holdout_v2), 2.4% (holdout_v3), 2.1%
(shifted_v3). The earlier 0% was small-sample luck — exactly what limits.md §7 warned
the 88-case number could not rule out. **The recommendation in results.md §7, "ship the
deterministic pipeline", does not survive a larger sample.** The decider does.

## 3. How it works

```
upload ─► bucket 1 + 2 rules ──► blocking issue? ──────────────► REQUEST_FIX
                 │
                 └► OpenCV features ─► guard fires? ───────────► ESCALATE (with the reason)
                                           │
                                           └► logistic p(defect) ─► < 0.176 ─► APPROVE
                                                                  └► else ───► ESCALATE
```

- **Rules keep the blocking checks.** A measured 150 DPI against a 300 DPI minimum is not
  a probability. The decider only sees files the rules would approve or escalate as
  advisory — the files where the human cost and the false-approve risk actually are.
- **The decider never writes to a customer.** A high p(defect) goes to a human. Only a
  rule measurement produces a fix request.
- **The model is plain JSON** (`capstone/models/safe_zone_lr.json`): reviewable in a
  diff, no pickle, no scikit-learn needed at inference. A test pins the JSON inference to
  scikit-learn's own probabilities.

### The feature that does the work

`measure_margin_objects` uses `cv2.connectedComponentsWithStats` to find every element
that reaches into the keep-out margin, then separates two things the old asymmetry
statistic could not:

- a **background band** — spans the full length of an edge; that is what bleed is for.
- an **element near the blade** — shorter than the edge; its depth is measured in
  safe-zone units from the inner margin line (0 = touching it, 1.0 = at the trim line).

On train, clean files sit between 0 and 0.95 and defects between 1.11 and 2.01.
`margin_depth_max` carries the largest coefficient in the model.

### Guards — what no decider is allowed to decide

| Guard | Why | Found by |
|---|---|---|
| No text detected | The detector fails toward finding nothing; empty is ambiguous | results.md §4.3 |
| Stroke or text within **half a pixel** of its limit | Rasterising rounds to the nearest pixel; the true width is on either side | case-00289: a 1.1× THIN_LINES defect measuring exactly 1.00×, same as two clean files |
| Element **merged into a background band** | Its outer edge is inside the band; depth is not in the pixels | shifted_v2: 7 of 10 top/bottom intrusions |

A full-pixel guard was tried first and rejected: at 150 DPI one pixel is almost the whole
0.5 pt limit, and it escalated 71 train files. Half a pixel caught exactly the three
undecidable ones.

## 4. What went wrong on the way (kept deliberately)

1. **Every clean file scored as an intrusion.** A full-width border also touches the side
   edges at its corners, where it is short. Judged by that side, every border looked like
   an element deep in the margin. Fixed by treating an object as background if it spans
   *any* edge it touches; pinned by a regression test.
2. **The generator's label text overruns the trim** on small products, and those files
   are labelled clean. Text is now excluded from the margin measurement, as it already
   was from stroke width. That is a concession to the labels, not a claim about print:
   see limits.md §11.
3. **The first shifted holdout failed** at 3.5% false-approve. Features were
   orientation-free by construction and tested from all four edges — but the tests used
   an empty canvas. The generator's top and bottom borders swallowed the marks. Fixed at
   the mechanism (`_band_bumps`), then validated on **new** seeds, because shifted_v2 was
   spent on the diagnosis.

## 5. Model selection (train split only, out-of-fold)

| Features | Trained on | Lowest defect p | Highest clean p | Separable |
|---|---|---|---|---|
| all 20 | decider scope | 0.054 | 0.519 | no |
| all 20, gradient boosting | decider scope | 0.015 | 0.182 | no |
| **5 safe-zone features, logistic** | **decider scope** | **0.352** | **0.287** | **yes** |
| all 20, logistic | all train files | 0.579 | 0.703 | no |
| all 20, gradient boosting | all train files | 0.365 | 0.353 | barely — a 0.012 gap |

*Before the half-pixel guard existed; the same comparison is what picked the model. Training
on all files spends the model on what the rules already reject, and the only separable
variant there has no margin to put a threshold in.*

The threshold is **half the lowest out-of-fold defect score** (0.176). An extra
escalation costs about $1.40 of reviewer time; a false approve costs a misprint; a
threshold fitted on 11 positives has no business sitting close to them.

## 6. Every run, for the record

| Set | Status | rules_only | cv_decider | Notes |
|---|---|---|---|---|
| train (312) | tuned on | 81.1% / 0.6% | 87.4% / 0.0% | in-sample |
| old holdout (88) | spent before this work | 82.0% / 0.0% | 88.0% / 0.0% | 44 approvals, UB 6.8% |
| holdout_v2 (600) | scored once, then re-run | 76.9% / 2.5% | 85.6% / 0.0% | unchanged by the fix |
| shifted_v2 (400) | **spent on diagnosis** | 72.5% / 4.4% | 80.8% / 3.5% → 78.8% / 0.0% | before → after fix |
| **holdout_v3 (600)** | **clean, scored once** | 78.9% / 2.4% | **84.4% / 0.0%** | |
| **shifted_v3 (400)** | **clean, scored once** | 79.6% / 2.1% | **84.6% / 0.5%** | 1 miss, below |

The one shifted_v3 false approve (case-00341) is a same-colour mark exactly 28 px tall
sitting inside a 28 px border band. The file is pixel-identical to a clean one. No
decider — this one, Jev, or Claude — can see it.

## 7. Where Jev fits

Jev takes structured input and returns calibrated probabilities over declared options,
which is the `Decider` protocol exactly. Adding it is one class and one harness arm; the
features, guards, and eval sets are shared. The decision rule, written down before any
Jev result exists (PLAN.md §11):

> Jev replaces the logistic decider only if it holds false approves at or below the
> logistic decider's on a fresh sealed holdout **and** either lowers the escalation rate,
> or matches it on a product the logistic model has no training labels for.

The honest prior: the logistic model already separates the train set perfectly
out-of-fold on five features. Jev's likely advantage is not accuracy here but day-one
coverage of a product line with no labels.

## 8. Reproduce

```bash
pip install -e ".[dev]"
python -m capstone.evals.train_decider --dry-run        # refit report, writes nothing

# regenerate a fresh set (images are gitignored; manifests are committed)
python -m capstone.data.generate -n 600 --seed 20260926 --clean-fraction 0.60 \
    --out holdout_v3.jsonl --cases-dir holdout_v3
python -m capstone.data.generate -n 400 --seed 20260927 --clean-fraction 0.60 \
    --intrusion-edges any --out shifted_v3.jsonl --cases-dir shifted_v3

python -m capstone.evals.harness cv_decider --manifest capstone/data/holdout_v3.jsonl --split all
```

`--split all` is correct for these sets: none of their cases was used for training or
tuning, so the whole manifest is held out. The `split` field inside them is the
generator's default and means nothing here.

## 9. What this does not show

- **Synthetic art only.** Real borders are not flat, so `_band_bumps` will fire more on
  real files. The cost is escalations, not false approves, but the rate is unmeasured.
- **The margin feature and the generator share a definition** of a safe-zone defect
  (depth past the trim line). That is the same circularity as every bucket-2 check —
  limits.md §1 — and the shifted set tests orientation, not that.
- **Text at the blade is caught by nothing.** Excluded from the margin measurement
  because the labels call it clean (limits.md §11).
- **The Claude comparison rests on 44 approvals.** The decider has 1,000 fresh cases
  behind it; Claude has 88. A fair head-to-head at scale needs an API sweep (~$4 for
  600 cases at the measured $0.0066/file).
