# The rule loop: a model proposes, measurement and a person decide

**Adopted:** 2026-09-26 · **Status:** round 1 in progress (§5)

> **Unofficial project.** Not affiliated with Sticker Mule.

The pipeline never runs a model to decide a file (decider.md). Its *rules*, though, can
improve faster if a model reads the failures and proposes changes. Every engine and
builder fix in ai-art.md came from that loop, with the model as proposer: the cut-out
halo, the drawn-width stroke label, the checkerboard valley test. This document fixes
how the loop runs, so that the rules keep coming from evidence and not from a model's
confidence.

## 1. Roles

| Role | Who | Never does |
|---|---|---|
| Proposer | a model (in practice Claude, in a working session) | ships a rule, sees the sealed set a rule is judged on |
| Ground truth | a person: a reviewer's decision, or a labelling round | — |
| Decider | the project owner: adopts or rejects each rule | adopts a rule that failed its pre-registered test |
| Judge | the eval harness on a fresh sealed set, scored once | — |

## 2. The steps

1. **Collect disagreements:** engine vs label, and better, engine vs a person. Reviewer
   decisions from `ops/review_queue` are the best source: labels cover only the
   defects we injected, and a person judges what the image itself contains.
2. **Read and group** (proposer). Measurements plus image crops, grouped into named
   failure modes. **At most three rule hypotheses a round,** each with its expected
   effect.
3. **Confirm on pixels** before any code. A model's mechanism is a hypothesis until the
   pixels agree. (ai-art.md §4: "5 exclusion slivers" was wrong; 4 were a label
   rounding error.)
4. **Implement off by default**, as a named constant or flag, so the shipped behaviour is
   unchanged until adopted (`bucket2_pixels.STROKE_MIN_DETAIL_IN` is the pattern).
5. **Price on spent sets:** approve rate, wrong approvals with the exact bound, newly
   approved files, the red team, and real art as well as the target set
   (`evals/price_detail_rule.py` is the pattern).
6. **Pre-register** the rule and its pass criteria in a commit, **then** build a fresh
   sealed set, score it once, and adopt or reject. A rejected rule stays in the history
   with its numbers.

## 3. Guardrails, each learned the hard way

- **Hypothesis budget.** Three per round, pre-registered. Fifty tries until one passes is
  how the first checkerboard check fired on 1.5% of clean AI images by chance.
- **Content needs a person.** "0 wrong approvals" is by label, and labels cover injected
  defects only. A rule about what an image *contains* is validated against human
  decisions, never against labels alone (ai-art.md §9).
- **Controls in every labelling round.** A share of items has a known answer (a caption
  we injected at a known size). Low agreement on the controls means the round is not
  used.
- **The proposer never sees the judge's set.** Sealed sets are built after the rule is
  frozen, from images no diagnosis touched.
- **The shipped constraint never moves.** SC-002 (wrong approvals ≤ 1%, on the bound) is
  the test every rule must pass, on real art as well as the target set.

## 4. Runtime advice (separate, and narrow)

A model may attach a *note* to a file that is already escalated, for the reviewer: "the
2 pt lettering reads as garbled 'Slakeboard'". It never changes a verdict. The
reviewer's decision on that file then feeds step 1.

## 5. Round 1: garbled lettering on AI art

**Question.** On AI-generated art, TEXT_TOO_SMALL blocks many files labelled clean
(ai_art_v1: 93 of 624 clean files; v2: 86). Samples suggest most of it is Stable
Diffusion's garbled small lettering, plus some art detail the detector boxes as text.
Is that lettering text a customer needs to read (it should block), or decoration that
may print as drawn?

**Ground truth.** A labelling page shows each flagged region, with the whole sticker,
and asks one question with four answers: *real text, too small* / *garbled or decorative
lettering* / *not text* / *can't tell*. Items are the TEXT_TOO_SMALL-blocked clean files
of ai_art_v1, plus hidden controls (captions injected below the minimum, whose right
answer is *real text, too small*), shuffled.

**Use.** If the controls agree at least 90% of the time, the labels drive at most three
rule hypotheses (step 2), and the rest follows §2.

### Round 1 result: the consistency gate failed (85% < 90%)

Labelled 2026-09-26, all 113 items (`evals/labels/lettering_round1.jsonl`, with the key).
Controls: 17 of 20 "real text", 3 "can't tell", none answered wrongly. Under §3 the round
is **not used for rules**.

Diagnosed afterwards: the fault was the page, not the labeller. The close-up padded the
crop by twice the box's *width*, so a wide, thin caption showed about 7 px tall. 16 of the
20 controls were shown under 14 px tall, and all 3 misses are among them; the 4 controls
shown legibly were all answered correctly. 25 of the 93 main items were shown under 14 px
too. Two main items looked like "our caption called not text", but on inspection the box
was on the AI image's own detail (the position filter misfired), and "not text" was right.

### Round 1b: fix the display, then measure consistency (pre-registered)

A new close-up crops to the box (about 5× its height, its width plus 30%), so letters
show 25-60 px tall. 60 items, shuffled, new ids:

- the 25 main items round 1 showed under 14 px, labelled again;
- 20 new controls: injected sub-minimum captions from ai_art_v1/v2, each confirmed by eye
  to be a legible caption under the new zoom (not a bar, not art);
- 15 main items round 1 showed legibly, repeated unannounced (test-retest).

**Pass:** controls ≥ 90% "real text" **and** repeats ≥ 80% identical to round 1. **If it
passes,** the round-1 labels are used, with the 25 small-shown items replaced by their 1b
answers. **If it fails,** no rule is proposed from these labels.
