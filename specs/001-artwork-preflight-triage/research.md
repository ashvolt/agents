# Phase 0 Research: Artwork Preflight Triage

Decisions taken before the data model was fixed, each with the alternatives that were
rejected and the reason. Per Constitution Principle VII, rejected options are recorded
with their arithmetic rather than deleted.

**Date:** 2026-09-22

---

## D-1 — Check assignment: three buckets, not two

**Decision.** Checks are assigned to metadata (bucket 1), pixel analysis (bucket 2), or
judgement (bucket 3). Only `CONTENT_IN_SAFE_ZONE` and an unstructured gestalt assessment
reach the model.

**Alternatives rejected.**

| Option | Rejected because |
|---|---|
| Two buckets (deterministic / judgement), as originally written in brief §8 | Four of the five "judgement" checks are measurement problems. Stroke width is a morphological erosion; contrast is a ΔE; transparency is the alpha channel; text height is arithmetic once boxes are known. Sending them to a model is slower, costlier and less accurate. |
| All checks deterministic | `CONTENT_IN_SAFE_ZONE` needs a judgement Python cannot make. Ink inside the cut margin is a bounding-box test; whether that ink is a deliberate bleed or a logo about to lose its top third is not. |
| All checks to the model | Asking an LLM to compute DPI when the number is in the file header. Fails Principle III outright. |

**Consequence.** Vision spend drops (one check, not five), latency drops, and the
`--no-tools` control arm now measures a much stronger claim.

## D-2 — Self-hosted vision model: rejected

**Decision.** Claude vision for bucket 3. No self-hosted VLM.

**Arithmetic.** Image tokens ≈ `(width × height) / 750`; artwork resized to ~1100px is
~1,600 tokens.

| Model | per image | at 4,000 files/day | per year |
|---|---|---|---|
| Haiku 4.5 | $0.0016 | $6.40 | ~$2.3K |
| Sonnet 5 | $0.0032 | $12.80 | ~$4.7K |
| Opus 5 | $0.0080 | $32.00 | ~$11.7K |

One L4-class GPU on demand, continuous, is ~$7K/year *before* redundancy, serving stack,
or operator time. **Self-hosting costs more than Haiku at this volume.** Crossover is near
30,000 files/day — roughly 7× the assumed volume.

Two further reasons independent of cost:

1. Escalation is threshold-based, so **calibration** matters more than raw accuracy. Small
   open VLMs are weakest at spatial reasoning and at knowing when they are unsure. A
   confidently-wrong model goes straight through SC-002.
2. Adding a second model family before a baseline exists makes every later eval movement
   un-attributable.

**Revisit when** volume exceeds ~30K files/day or bucket 3 grows back.

## D-3 — A local model *is* used — but not a language model

**Decision.** `TEXT_TOO_SMALL` uses a CPU text **detector** for bounding boxes; Python does
the point-size arithmetic.

Purpose-built detectors (PaddleOCR, Tesseract, CRAFT) beat any VLM at locating text, run on
CPU, cost nothing per call, and return identical boxes every run — which SC-008 requires.

**Open (OQ-3):** which detector. Decided by measured recall at small point sizes, the regime
that matters and the one detectors are weakest in. The interface is defined now
(`detect_text(image) -> list[TextBox]`) so the implementation is swappable without touching
the checks.

**Interim.** A deterministic stub detector ships first so Phases 1–4 are unblocked and
offline-testable. It is explicitly not the production path.

## D-4 — Eval circularity: straddled thresholds

**Problem.** "Labels correct by construction" is a genuine strength for bucket 3, where
generator and grader share nothing. It is a **weakness** for buckets 1 and 2: inject a
0.2pt line, assert the 0.5pt checker flags it, and the test passes by construction while
measuring nothing. That is grading a ruler against itself.

**Decision.** Every deterministic defect is injected at magnitudes straddling the
threshold — approximately 0.5×, 0.9×, 1.1×, 2× the spec limit. Clean cases likewise sit
deliberately just *inside* the limit.

**Rationale.** A checker that only sees obvious cases never reveals an off-by-one in unit
conversion or an inclusive/exclusive boundary bug. More importantly, borderline files are
where the money goes — a file at 0.9× the DPI limit is the one a human would argue about
and the one a false approve comes from.

**Residual risk.** Generator and checker still share my assumptions about what a defect
*is*. Documented in `limits.md`; not fully curable with synthetic data.

## D-5 — Deterministic checks run before the vision call

**Decision.** Buckets 1 → 2 → 3, in order.

**Rationale.** A file with a citable defect needs no judgement call, so the vision call is
skipped and cost avoided. When the call does happen, the measurements are supplied as
context, which materially reduces invented findings. The model is asked to judge, not to
measure.

## D-6 — `finalize()` as the single verdict chokepoint

**Decision.** Every exit path returns through one function. `APPROVE` is reachable from
exactly one branch, guarded by an explicit "every applicable check ran and passed"
predicate.

**Alternative rejected.** Returning verdicts from wherever they are determined. It works
until an exception handler somewhere returns a default, and a default that is ever
`APPROVE` violates Principle IV silently. A chokepoint makes the invariant testable:
`test_agent_failure_paths.py` injects every failure and asserts none produces `APPROVE`.

## D-7 — Prompt caching treated as architecture

**Decision.** System prompt and tool list are frozen module-level strings assembled in a
fixed order. Per-file data goes after the last cache breakpoint. The harness reports
`cache_read_input_tokens`.

**Rationale.** Caching is prefix-match; any byte change invalidates everything after it. At
~20K input tokens per file and 4,000 files/day, cached reads are the difference between the
cost target being reachable and not (see OQ-1). That makes it load-bearing rather than an
optimisation, which is why it is in the plan rather than a later tuning pass.

**Detection.** `cache_read_input_tokens == 0` across repeated calls means a silent
invalidator — a timestamp, a UUID, an unsorted dict, a varying tool list. The harness
surfaces it as a defect, not a curiosity.

### Amended 2026-09-23 — caching does not engage on this build

Measured: the cacheable prefix is **1,696 tokens** (system prompt ~720 + tool schemas
~976). Haiku 4.5 requires a minimum cacheable prefix of **2,048 tokens**, so the
`cache_control` breakpoint is a **silent no-op**. Nothing is cached, and nothing will be
until the prefix grows past the minimum.

Three consequences, all worth stating plainly:

1. **`cache hit rate: 0%` in a sweep report is expected on this build**, not the silent
   invalidator the metric was written to catch. `SweepReport.cache_suspect` will fire and
   be wrong. Read it alongside this note until the prefix crosses 2,048.
2. **The "caching is load-bearing" claim above was premature.** It is load-bearing for the
   *realistic* token shape (20K input/file) this design was sized against. At the actual
   shape of this dataset it does nothing, because the whole request is small.
3. **Padding the prefix to reach the minimum would be cargo cult.** The cost target is
   already met without it (D-9). Caching earns its place when real-sized artwork and a
   longer system prompt push the prefix over 2,048 naturally, not before.

The design decision stands — keep the prefix byte-stable, keep volatile content after it —
because it costs nothing and is correct the moment the prefix grows. What changes is the
claim about what it currently buys: nothing.

## D-8 — Storage: JSONL on the filesystem

**Decision.** Cases, gold labels, traces, and run results are JSONL. No database.

**Rationale.** ~200 cases. JSONL is diffable, greppable, and reviewable in a PR. A database
adds a dependency, a migration story, and a local-setup step for zero benefit at this scale.

**Revisit when** trace volume exceeds what a file scan handles comfortably.

## D-9 — Cost target may be unreachable (OQ-1)

**Finding.** SC-003 targets $0.01/file. At the assumed 20K in / 2K out shape:

| Model | cost/file at list price |
|---|---|
| Opus 5 | $0.1500 |
| Sonnet 5 | $0.0600 |
| Haiku 4.5 | $0.0300 |

The cheapest model is **3× over budget**; Opus is 15× over. The brief currently states a
budget that list pricing cannot meet.

**Levers, to be measured in Phase 2.**

- Batch API: −50% on non-interactive sweeps.
- Prompt caching: cached input reads at ~10% of normal. With ~20K input mostly cached, the
  input side falls sharply.
- Shrinking the token shape: fewer loop turns, a tighter system prompt, no full-resolution
  image when a downscale suffices.

**Resolution rule.** Phase 2 measures the real per-file cost. Either SC-003 holds with
caching plus batching — in which case D-7 is confirmed load-bearing — or SC-003 is amended
with the measured number and the ROI model in brief §4 is corrected. Whichever happens gets
written down.

This was caught by writing a cost function before building the feature, which is exactly
what [levels/L0_raw_api](../../levels/L0_raw_api/) exists to teach.

### Resolved 2026-09-23 — SC-003 holds, but for a reason that does not generalise

Measured token counts for this build, on Haiku 4.5 ($1/$5 per MTok):

| Component | tokens |
|---|---|
| system prompt | ~720 |
| tool schemas | ~976 |
| **image, mean over the 200 cases** | **~452** (median 318, max 1,448) |
| order context | ~85 |
| all three tool results | ~311 |

| Loop shape | cost/case | 159-case sweep |
|---|---|---|
| 2 model calls (tools in parallel) | **$0.0073** | ~$1.16 |
| 4 model calls (sequential) | **$0.0146** | ~$2.31 |

**SC-003 ($0.01/file) is met at the 2-call shape and missed at the 4-call shape** — which
makes the number of loop turns, not the model or the price list, the thing that decides
whether the cost target holds. Turn count is the lever worth tuning in Phase 5.

**The caveat that matters more than the result.** The original estimate assumed ~1,600
tokens per image. The measured mean is ~452, because the synthetic art is small — a 2x2 in
sticker at 150 DPI is 338 px on its long edge. Real customer uploads are routinely
2,000-4,000 px, which lands at the 1,100 px downscale cap and costs roughly 1,600 tokens,
**3.5x the measured figure**.

So SC-003 passes here *because the test images are small*, which is a property of the
dataset rather than of the design. On realistic artwork the 4-call shape would cost about
$0.018/file and breach the target. Recorded in `limits.md` §6 rather than reported as a
clean pass.

Batch (-50%) remains unused and is the obvious lever if the realistic shape needs it.
Caching is not available at this prefix size — see the D-7 amendment.
