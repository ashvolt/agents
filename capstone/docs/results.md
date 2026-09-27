# Results — Artwork Preflight Triage

What was measured, on what, and what it means. Every number here comes from a run in
`capstone/evals/runs/`, reproducible from the manifest and the seed.

**Date:** 2026-09-23 · **Model:** `claude-haiku-4-5` · **Total API spend:** ~$4.30

> **Unofficial project.** Not affiliated with Sticker Mule. All artwork is synthetic and
> generated locally. Volume and cost figures are labelled assumptions.

> **Superseded in part, 2026-09-23 — see [decider.md](decider.md).** On 600-case fresh
> holdouts rules_only breaches SC-002 (2.1-2.5%); the 0% below was small-sample luck. A
> CV decider with no vision model passes (84.4% / 0.0% and 84.6% / 0.5%) and matches
> Claude's 13.6% escalation rate on the set below at $0 per file. This page is kept as
> written, because the progression is the record.

---

## 1. Headline

Both success criteria are met, on a held-out split scored once.

| | SC-001 auto-approve ≥60% | SC-002 false-approve ≤1% | |
|---|---|---|---|
| **rules_only** (no model) | **82.0%** | **0.0%** (0 of 41) | PASS |
| **agent_fast** (Claude vision) | **82.0%** | **0.0%** (0 of 41) | PASS |

**The deterministic pipeline meets both criteria without the model.** That is the central
result, and it was not the expected one.

## 2. The honest comparison

Holdout, 88 cases (50 clean / 38 defective), scored once after all tuning was frozen:

| Metric | rules_only | agent_fast | Difference |
|---|---|---|---|
| auto-approve | 82.0% | 82.0% | none |
| false-approve | 0.0% | 0.0% | none |
| false-reject | 18.0% | 18.0% | none |
| borderline recall | 100% | 100% | none |
| per-issue recall, all 10 codes | 100% | 100% | none |
| **escalation rate** | **17.0%** | **13.6%** | **−3.4 pp** |
| cost per file | $0.0000 | $0.0066 | +$0.0066 |

One difference, in one metric: the model resolves 3 of the 88 files that the
deterministic pipeline sends to a human.

### Does that pay for itself?

Under the brief's assumptions — 4,000 files/day, $28/hour loaded artist cost, ~3 minutes
per escalated review:

```
escalations avoided  = 3.4% x 4,000        = 136 files/day
labour saved         = 136 x 3 min         = 6.8 hours/day
value                = 6.8 x $28           = $190/day
model cost           = 4,000 x $0.0066     = $26/day
                                             ---------
net                                          ~$164/day   (~$60K/year)
```

**Narrowly positive.** The model earns its place on human-load reduction — not on safety,
not on approve rate, and not on detection.

**Caveat that matters:** the difference is 3 cases out of 88. That is directional, not
significant. A correct reading is "the model did not hurt, and may reduce escalations";
confirming it needs several hundred more cases.

## 3. What the model actually contributed

On the full 312-case train split, before the final gates:

| Code | rules_only | agent_fast |
|---|---|---|
| `CONTENT_IN_SAFE_ZONE` | 58.8% | **64.7%** |
| every other code (9 of them) | — | **identical** |

The vision pass changed exactly one check, by one detection per 312 files. Nine of ten
checks were unaffected because they are measurements, and Python measures them exactly.

This is the `--no-tools` control arm doing the job brief.md §8 promised: proving the
code/model split rather than asserting it. The measured answer is that the split is real,
and the model's share of it is much smaller than the design assumed.

## 4. The three findings that changed the build

### 4.1 The tool loop made results worse, not just costlier

Identical 50 cases:

| | tool loop | single call |
|---|---|---|
| auto-approve | 72.7% | **81.8%** |
| false-approve | 33.3% | **18.2%** |
| safe-zone recall | 1/7 | **4/7** |
| cost/file | $0.0117 | **$0.0067** |

A tool loop lets the model choose which measurements it needs. That is worth paying for
when tools are slow or expensive. Every tool here is deterministic Python costing
microseconds, so the choice buys nothing — and the model was second-guessing measurements
it could simply be handed. **Removing its opportunity to choose improved accuracy and cut
cost 1.75x.**

### 4.2 The headline metric was unmeasurable, and the fix was the dataset

SC-002 is false approves ÷ approvals. With 65 clean cases the split yields ~46 approvals,
so one wrong approval scores 2.2% — the metric could return 0% or ≥2.2%, never 1%.

Same code, two dataset sizes:

| eval set | approvals | resolution | rules_only false-approve |
|---|---|---|---|
| 50 cases | 22 | 4.5% | **33.3%** |
| 312 cases | 179 | 0.6% | **3.9%** |

**The number fell by a factor of eight with no code change.** Everything being tuned
against on the small set was quantisation noise. The harness now reports the resolution
and a 95% upper bound, and refuses to let a run claim more precision than its sample size
supports.

### 4.3 Absence of evidence kept being read as evidence of absence

This failure appeared three times, in three different places:

1. The text detector returns nothing for merged glyphs, short lines and rotated type. An
   empty result was being treated as "no text here."
2. A safe-zone measurement that said "symmetric — consistent with deliberate bleed" below
   its threshold **halved safe-zone recall**, from 43% to 14%: a confident all-clear from
   a signal that misses a third of real intrusions, and the model believed it.
3. Two of the last five false approves were files with text at twice the minimum size that
   the detector never saw.

It is now policy: **a detector that finds nothing escalates.** That gate plus a tightened
safe-zone threshold is what took false approves from 3.1% to 0.6% on train.

## 5. The path to passing

Tuned on train only. The holdout was untouched until section 1.

| safe-zone threshold | no-text gate | approve | false-approve | wrong |
|---|---|---|---|---|
| 0.06 | no | 83.2% | 3.1% | 5 |
| 0.03 | no | 77.9% | 2.0% | 3 |
| **0.03** | **yes** | **76.3%** | **0.7%** | **1** |
| 0.02 | yes | 68.4% | 0.0% | 0 |

Each tightening step buys precision at roughly 8 points of approve rate. That is the
SC-001/SC-002 trade, priced rather than assumed. 0.03 plus the no-text gate was chosen as
the highest approve rate that still clears the constraint.

## 6. Cost

| | measured |
|---|---|
| cost per file, single call | $0.0066 |
| cost per file, tool loop | $0.0117 |
| SC-003 target | $0.01 |
| p95 latency | 11.1 s (SC-004 target 20 s) |
| full 312-case sweep | $2.02 |

SC-003 is met — **but for a reason that does not generalise.** The mean image in this
dataset is ~452 tokens because a 2×2in sticker at 150 DPI is 338 px. Real uploads are
2,000–4,000 px and cost roughly 1,600 tokens, about 3.5× more. Treat SC-003 as unproven
for production until measured on real-sized files. See [limits.md](limits.md) §6.

Prompt caching contributes nothing: the cacheable prefix is 1,696 tokens against Haiku's
2,048 minimum, so `cache_control` is a silent no-op here.

## 7. Recommendation

**Ship the deterministic pipeline. Keep the model, but know what it is buying.**

1. Buckets 1 and 2 plus the two escalation gates meet both success criteria at zero
   marginal cost and ~200 ms per file. That is the system of record.
2. The vision pass is justified only by the escalation reduction in §2, worth roughly
   $164/day against the assumptions — and that estimate rests on 3 cases. **Run it as a
   measured experiment on live traffic before committing to it.**
3. If it does not reproduce at scale, remove it. Nothing else depends on it.

> **Update 2026-09-23:** point 1 did not survive a larger sample — rules_only breaches
> SC-002 at n=600 (limits.md §12). The vision pass was removed and replaced by a
> zero-cost CV decider rather than kept: [decider.md](decider.md).

## 8. What these numbers do not show

- **Synthetic artwork only.** Real files carry ICC profiles, layers, vector/raster mixes
  and fonts that do not render as expected. Every score here is an upper bound.
- **The generator and the checks share assumptions.** Straddled thresholds catch boundary
  and unit bugs — they caught four — but not an assumption wrong in both places.
- **The holdout is small.** 41 approvals; zero false approves gives a 95% upper bound of
  7.3%, not 0%.
- **No adversarial cases.** The red-team suite does not exist, so SC-006 has no
  measurement behind it. Injection via rendered image text is the attack this design
  invites and it is untested.
- **`THIN_LINES` has a false-reject band** of 0.50–0.60 pt at 300 DPI, caused by
  rasterisation. See [limits.md](limits.md) §4.

Full list: [limits.md](limits.md).
