# The customer message: two registers, one finding

*2026-09-29. Text and schema only — no verdict behaviour changed, so no eval number moves.
See §5.*

## 1. The observation

A real `REQUEST_FIX` line, as a customer received it:

```
Artwork is 138 DPI at the ordered size of 5x3 in. Die-cut sticker needs at least
150 DPI or the print will look soft.
  (measured 137.9 dpi, requires 150 dpi)
```

Three things are wrong with it, in ascending order of cost.

**It reads as machine-assembled.** The same value is rendered twice, two lines apart, two
ways: `{dpi:.0f}` gives 138 and `Evidence.describe`'s `:g` gives 137.9. A designer reads
that as sloppy; everyone else reads it as "which is it?"

**It is not actionable by the person receiving it.** DPI is a ratio — pixels over inches —
so it means nothing without the print size, and it is not reusable, because `min_dpi` is 72
on a banner and 300 on a roll label. A customer who learns "150" here carries it wrong to
their next order. Worse, DPI is not a setting anyone can find: it is a property of the file
*and* the order together.

**It teaches the customer how to defeat the check.** This is the one that matters.
"Needs at least 150 DPI" is phrased as a target, and the obvious way to hit a target is to
type it into a box. Plenty of tools will happily set document DPI to 150 — which resamples
the image, produces a file that **passes `check_resolution` on re-upload**, and prints
exactly as soft as the original.

So the message's most likely reading invites the one action that satisfies the gate while
leaving the artwork broken. That is not brief.md §6 rank 2 ("wrong fix requested"). It is
rank 1, a false approve, manufactured by our own wording.

For the case above the actual gap is 64 pixels: 724 wide against 788 needed. "138 against
150" sounds badly wrong; 9% is usually one re-export away.

## 2. The root cause

One string was serving two readers. `Evidence.describe` says so in its own docstring —
*"for the escalation queue and customer message"* — and `_customer_message` fed
`Issue.message` to the customer verbatim as a bullet. A field tuned for two audiences ends
up tuned for neither, and the `.0f`/`:g` collision above is that compromise showing through.

Worth noting: the two-register idea was already designed in this repo. `narrate.Narration`
has carried `customer_message` and `reviewer_note` as separate fields since the narration
spike, and `narrate.SYSTEM_PROMPT` already states the register rule — *"plain language, no
ids, no jargon beyond 'bleed' and 'safe zone'"*. That module is parked (scene-narration.md),
and the policy was parked with it as a passenger. The policy did not need to be.

## 3. The change

`Issue.message` is unchanged and stays the production artist's line: exact, technical,
`137.9`. `Issue.advice` (`schemas.CustomerAdvice`) carries the customer's register in three
slots:

| slot | holds | example |
|---|---|---|
| `headline` | the consequence, in their terms | "Your die-cut sticker will print a little softer than it should." |
| `action` | what to do, in a unit they can act on | "The image is 724x448 pixels; a 5x3 in die-cut sticker needs 788x488. …export it again about 9% larger." |
| `avoid` | the plausible wrong fix | "enlarging this copy, or typing 150 into a DPI box — that adds pixels without adding detail" |

`avoid` is the slot that did not exist before and the reason the class earns its weight. It
is the only part of the letter that protects the press.

`src/customer_text.py` is the only thing that renders advice into prose, and both arms call
it. The deterministic arm and the agent arm previously worded the same letter differently,
so which letter a customer got depended on which arm ran.

**Layering, not simplifying.** Each finding renders as consequence → action → measurement,
the measurement last and labelled `Technical detail`. The number is kept, so a designer can
still act on it; it just no longer leads and is no longer the only thing present. The result
is *denser* than the sentence it replaced.

**`advice=None` is meaningful.** It means there is nothing for the customer to do, and
`customer_letter` drops the finding rather than inventing an action. Four findings carry no
advice on purpose: both safe-zone advisories, the text-detector-inconclusive advisory, and
the decider guard. All four exist to route a file to a person. Asking a customer to move
artwork we have not yet established is misplaced *is* brief.md §6 rank 2.

### Resolution advice branches three ways

One DPI number has three causes, and one shared fix would be false in two of them:

1. **No DPI tag** (`effective_dpi` fell back to pixels over the canvas) — pixels are the
   honest unit and the only one the customer can act on.
2. **A DPI tag, pixels already sufficient** — the artwork is fine, the file merely says to
   print it at the wrong size. Telling this customer to "export larger" sends them hunting
   for detail they already have.
3. **A DPI tag, pixels genuinely short.**

Branches 1 and 3 carry the anti-resample warning; branch 2 carries its own variant.

## 4. Open, and deliberately not done here

- **brief.md §12 OQ-4 is still open.** Whether the customer message needs its own quality
  eval, or hand review on a sample suffices. This change does not answer it. §5 lists
  message quality as a secondary metric — tracked, not optimised — so it should not eat a
  phase.
- **Branch 2 raises a rule question.** A file with ample pixels and a wrong DPI tag is
  flagged `LOW_RESOLUTION` although rescaling it would print perfectly. That may be a false
  reject. Changing it moves eval numbers, so it belongs in the rule loop (rule-loop.md),
  not in a text change. The advice is written truthfully for the current behaviour.

  **And it must not be papered over in the letter.** The first draft of branch 2 ended
  "reply and we will rescale it for you". That crosses §11's first non-goal — *"Not fixing
  the artwork. Detect and explain."* — by committing an artist to work the pipeline does
  not do, in text they may approve without reading closely. It was the worse mistake of
  the two below, because a possible false reject phrased as good service is a rule bug
  nobody will ever come back to fix. Branch 2 now explains and asks for the file, and the
  rule question stays a rule question.

- **No template may promise work on our side.** Two did: the rescale offer above, and an
  offer to move an order to a different product when transparency looked deliberate — the
  second crossing *"not pricing, scheduling, nesting, or anything downstream of
  approval"*. Both removed. Present-tense statements of what the pipeline already does are
  fine and stay ("we convert it to CMYK for printing"; the letter's "we'll re-check it
  right away"). `test_no_template_promises_work_on_our_side` holds the line.
- **No template may promise the annotated preview.** Two drafts referred to "the preview
  alongside this message". brief.md §11 says the customer-facing output is one generated
  message; nothing guarantees the image travels with it, and a dangling pointer is worse
  than none. Both were rewritten.
- **Still no model.** This is template text. The measured reason from registry.py stands:
  relaying findings through a model lost recall the tools already had (TEXT_TOO_SMALL 2/3 →
  1/3, WRONG_COLOR_MODE 1/1 → 0/1 on 50 cases). Facts stay engine-owned.

## 5. Why no eval number moves

Verdicts are untouched. `Issue` gained an optional field defaulting to `None`; every
decision path — thresholds, guards, the blocking/advisory split, `finalize` — is byte-for-byte
the same. The only behavioural surface is the text of `Verdict.customer_message`, which is
not scored by any metric in `evals/metrics.py`. 369 offline tests pass unchanged.

## 6. What the tests hold

`tests/test_customer_text.py`. Three of them are the point; the rest are rendering.

- **`test_no_template_advice_states_a_bare_dpi_target`** — no shipped customer action may
  name a DPI figure without an export instruction. This is the false-approve guard.
- **`test_every_customer_facing_blocking_code_carries_advice`** — a new blocking code
  cannot quietly ship with the artist's sentence as its customer letter.
- **`test_advice_survives_the_tool_payload_round_trip`** — the agent arm rebuilds issues
  from JSON, so advice that is not serialised is advice that arm loses in silence.

A fourth, added after review: **`test_no_template_promises_work_on_our_side`** — see §4.
It caught a live false positive on its first run (`MISSING_BLEED` warning that "we would
trim off the edges", which is a consequence of the customer's wrong fix, not an offer), so
the rule was narrowed to forward commitments and the sentence reworded to let the blade do
the trimming.

Every guard was mutation-checked; a guard that has never been seen to fail is decoration.
That is not ceremony — it found a real hole. Re-introducing the rescale promise into
branch 2 **passed**, because `CUSTOMER_FACING_ADVICE` only exercised branch 1, so the
guards had never seen branches 2 or 3 at all. All three branches are now listed, and the
same mutation fails as it should.

One more, because it caught a real bug in this change:
`test_every_parametrized_group_actually_holds_a_finding`. The unreadable-file case was
constructed without an `error`, so it produced no issue and every loop over it passed
without asserting anything.
