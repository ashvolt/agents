# Documented limits

Constitution Principle VII: documented limits are a deliverable, not an appendix. This
file says what the system cannot do and where it is known to fail. Everything here was
found by measurement or is a known property of the design — none of it is hypothetical
hedging.

**Last updated:** 2026-09-22, after the `rules_only` baseline.

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

## 4. Sub-pixel strokes are indistinguishable

At low DPI a stroke thinner than one pixel still rasterises to one pixel. A defect at
1.1x past the limit and one at 2.0x past it produce identical pixels, so per-magnitude
recall flattens at the bottom end.

This is physically real — a press cannot print half a pixel either — but it means
`THIN_LINES` recall on the most severe cases is not better than on the marginal ones, and
the eval cannot distinguish them.

## 5. Bleed and proportion are entangled

Without a trim box in the file, a file short on bleed necessarily has a different canvas
ratio, purely because of the missing margin. Reporting that as a proportion defect tells
the customer to fix something that is not wrong — the second-ranked failure mode in
brief.md §6.

The system therefore **suppresses `ASPECT_MISMATCH` whenever `MISSING_BLEED` fires**. A
file with both defects reports only the bleed problem, and the aspect problem surfaces on
resubmission. Real preflight tools behave the same way, but it does mean a two-defect
file produces a one-defect fix request.

## 6. Cost target is currently unmet at list price

SC-003 targets $0.01/file. At the assumed 20K in / 2K out shape:

| Model | cost/file, list price |
|---|---|
| Opus 5 | $0.1500 |
| Sonnet 5 | $0.0600 |
| Haiku 4.5 | $0.0300 |

The cheapest model is 3x over budget. Batch (-50%) and prompt caching (cached reads at
~10%) are the levers, and until they are measured **SC-003 is an aspiration, not a
result**. Tracked as OQ-1. The ROI model in brief.md §4 depends on this resolving.

## 7. Not yet measured at all

Honest status, not a roadmap:

- **The agent has never completed a sweep.** The API key in the working `.env` is
  invalid, so the only agent numbers in this repo are from the degradation path.
- **No held-out score exists.** The holdout split is sealed and will be read once, at the
  end (T110). Any number quoted before then is a train-split number.
- **Confidence threshold is a placeholder** (0.70). OQ-2 says fit it on the train split;
  it has not been fitted.
- **Prompt caching is designed for but unverified.** `cache_read_input_tokens` has never
  been observed non-zero, because no successful call has been made.
- **The red-team suite does not exist.** Injection resistance is designed
  (`injection_suspected` forces escalation, image content is framed as untrusted data)
  but untested. SC-006 has no measurement behind it.
- **MCP server, CI gate, HITL queue, runbook alerts** — not built.

## 8. Design choices that are limits by intention

- **Not multi-agent.** One agent plus deterministic tools. If the evals ever show a single
  agent cannot meet SC-001/SC-002, that gets revisited with evidence.
- **No self-hosted vision model.** Costs more than Haiku below ~30K files/day, and
  calibration matters more than raw accuracy when escalation is threshold-based.
  Arithmetic in research.md D-2.
- **No database.** JSONL on disk. Fine at 200 cases; revisit when trace volume outgrows a
  file scan.
- **The product spec table is invented.** Plausible for the class of product, but not
  sourced from a real print operation. Every threshold in it is an assumption.

## 9. What would change my confidence most

In order:

1. A hundred real customer files with real reviewer decisions. Everything above is
   downstream of not having them.
2. A successful agent sweep, so the model's contribution is measured rather than argued.
3. The red-team pass, because injection via rendered image text is the attack this design
   invites and it is currently untested.
