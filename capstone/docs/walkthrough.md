# Walkthrough — how this project works, and how to defend it

A reading order and a question bank. Work through it once and you can explain any part of
this repo under pressure.

**This is the only document here written for you rather than for a reader of the repo.**
Everything else is the deliverable; this is the study guide.

---

## 0. The one-paragraph version

*"A print shop has a person check every uploaded artwork file before printing. Most files
are fine, so most of that time is spent confirming nothing is wrong. I built a system that
clears the obviously-clean files automatically and sends everything else to a human with
evidence attached. The interesting part is that I built the evaluation harness first, and
it told me the LLM was doing almost none of the work — nine of ten checks are exact
measurements that Python does better. So I shipped the deterministic pipeline and kept the
model as a measured experiment."*

If you can say that and mean it, the rest is detail.

## 1. Read the code in this order

Each file, the one idea in it, and roughly how long.

| # | File | The one idea | Time |
|---|---|---|---|
| 1 | `src/schemas.py` | The `Verdict` validators make unsafe states **unrepresentable** | 20 min |
| 2 | `src/product_specs.py` | Unknown product **raises** — a guessed spec is a silent false approve | 5 min |
| 3 | `tools/bucket1_metadata.py` | Exact measurements from file headers; resolution and bleed **decoupled** | 20 min |
| 4 | `tools/text_detect.py` | A real connected-components detector, with its blind spots documented | 15 min |
| 5 | `tools/bucket2_pixels.py` | Four "judgement" checks that turned out to be measurements | 30 min |
| 6 | `data/generate.py` | Defects injected, so labels are correct **by construction** | 25 min |
| 7 | `evals/metrics.py` | The metric reports its own **resolution** | 20 min |
| 8 | `evals/harness.py` | Runs any callable; holdout not readable by accident | 15 min |
| 9 | `evals/baselines.py` | `rules_only` — the number the agent had to beat | 10 min |
| 10 | `src/agent.py` | `finalize()` — one exit, one guarded `APPROVE` branch | 40 min |
| 11 | `evals/gate.py` | CI goes red on quality, not just on bugs | 10 min |

**Then read the tests**, in this order: `test_schemas.py` → `test_agent_failure_paths.py`
→ `test_bucket1.py`. The tests are the spec made executable, and the failure-path suite is
where the safety argument actually lives.

## 2. The five things you must be able to defend

### 2.1 Why the false-approve rate is the metric, not accuracy

Accuracy averages over the one failure that destroys the business case. A defective file
sent to press costs a reprint, a reship, a ticket and reputational damage; a clean file
sent to a human costs forty seconds. Roughly 10:1. So the system is deliberately
asymmetric — eager to escalate, extremely reluctant to approve — and the metric has to be
asymmetric too.

**Where it lives:** `brief.md` §5–6, `metrics.py`.

### 2.2 Why `false_approve_rate` divides by approvals, not by defective files

The question the metric answers is *"how much can I trust an APPROVE?"*, because that is
what decides whether the human comes out of the loop. Dividing by total defectives instead
lets a system that approves almost nothing report a flattering number.

**Where it lives:** `metrics.py`, the `false_approve_rate` docstring.

### 2.3 Why `finalize()` is a chokepoint rather than a convention

Every exit path — success, exception, budget exhaustion, schema failure, refusal,
truncation — returns through one function, and `APPROVE` is reachable from exactly one
branch guarded by five conditions. A convention survives until someone adds an `except`
with a default. A chokepoint plus nine injected-failure tests fails the moment that
happens.

**Where it lives:** `agent.py::finalize`, `test_agent_failure_paths.py`.

### 2.4 Why the three-bucket split, and why it moved twice

Bucket 1 is file metadata, bucket 2 is pixel analysis, bucket 3 is judgement. Both moves
were **toward code**: four checks started in bucket 3 and were measurement problems
(stroke width is an erosion, contrast is a ΔE, transparency is the alpha channel, text
size is arithmetic once you find the boxes); then safe-zone turned out two-thirds
computable via opposing-edge asymmetry.

**Where it lives:** `research.md` D-1, `results.md` §3.

### 2.5 Why you shipped without the model in the hot path

Because the numbers said so. Across 312 cases the model changed exactly one check by one
detection. On the holdout both arms scored identically except escalation rate. Your own
plan said *"nothing ships unless it beats the previous number."*

**Where it lives:** `results.md` §1–3.

## 3. Question bank

Questions you should expect, and where the answer is.

### On evaluation

**"How do you know it works?"**
400 synthetic cases, defects injected so labels are correct by construction. 312 train,
88 holdout sealed at generation and scored once at the end: 82.0% auto-approve, zero false
approves in 41 approvals.

**"Isn't synthetic data cheating?"**
Partly, and it is the biggest limitation — `limits.md` §1. It buys correct labels with no
hand-labelling and no annotator disagreement. It costs realism: the generator and the
checks share my assumptions, so straddled thresholds catch boundary and unit bugs but not
an assumption wrong in both places. Every score is an upper bound.

**"What's the weakest number in the project?"**
Zero false approves on 41 approvals carries a 95% upper bound of 7.3%, not 0%. Small
holdout. Say this before they find it.

**"Your false-approve rate went from 33% to 3.9% — what did you change?"**
Nothing. The sample size. At 22 approvals one error scores 4.5%, so the metric could not
represent 1% at all. That is why the harness now prints its own resolution.

### On architecture

**"Why not just use an LLM for everything?"**
Asking a model to compute DPI when the number is in the file header is slower, costlier
and less accurate. Measured: nine of ten checks identical between arms.

**"Why did you remove the tool loop?"**
It was worse *and* costlier — 72.7%/33.3% against 81.8%/18.2%, $0.0117 against $0.0067. A
loop lets the model choose which measurement it needs; that is worth paying for when tools
are slow. Mine are deterministic Python costing microseconds, so the choice bought nothing
and the model spent turns second-guessing measurements it could be handed.

**"When would you keep the tool loop?"**
When tools are expensive, slow, have side effects, or when which-tool-next genuinely
depends on earlier results. None held here.

**"What would make you add the model back to the hot path?"**
Real customer files. The safe-zone heuristic is calibrated on synthetic artwork where
every intrusion is one block on one edge. On real files it will not transfer, and the
judgement call the model makes will matter more.

### On production

**"What happens when the API is down?"**
Deterministic checks still run, the verdict is `ESCALATE` with `degraded=True`, and the
schema forbids `APPROVE` in degraded mode. The queue gets longer, which is the status quo
before the system existed.

**"What pages you at 3am?"**
False-approve rate above 1%. Nothing else. Escalation spikes, outages and budget
exhaustion all degrade toward a human, which is the designed safe state.

**"What's the worst realistic incident?"**
A change that quietly raises approvals while raising false approves. Throughput improves
and the queue shrinks, so it looks like a win on every dashboard except the one that
matters. That is what the CI gate exists for.

**"Show me the gate working."**
`capstone/evals/gate.py`. Delete the no-text escalation line and it goes red with two
findings: SC-002 breach at 1.88%, and `TEXT_TOO_SMALL` recall 100% → 90%. Restore, green.

### On the LLM itself

**"How do you handle prompt injection?"**
Image content is framed as untrusted data, the system prompt is the only instruction
channel, nothing from the file enters system context, and `injection_suspected` forces
escalation. **Then say: it is untested.** The red-team suite does not exist, so SC-006 has
no measurement. It is the largest open gap.

**"What does a run cost?"**
$0.0066/file measured, against a $0.01 target — but that passes because the synthetic
images are small (~452 tokens vs ~1,600 for realistic artwork). Unproven for production.

**"Why is your cache hit rate zero?"**
The cacheable prefix is 1,696 tokens; Haiku's minimum is 2,048. `cache_control` is a
silent no-op here. Padding the prefix to reach the minimum would be cargo cult.

## 4. The three war stories

Have these ready. They are what separate you from someone who read a tutorial.

**Cost accounting reported $0.00 for a whole sweep — two independent bugs.** The API
returns a dated snapshot id (`claude-haiku-4-5-20251001`) that the pricing table did not
have, so pricing raised and cost fell to zero. Separately, `python -m` ran the harness as
`__main__` while the agent imported it as a package, creating two `TRACE_SINK` dicts —
writes to one, reads from the other. Both silent, both looked like good news. And the
spend cap read the same zeroed number, so the budget guard was inert too.

**Handing the model a measurement made it worse.** I added a safe-zone signal whose
payload said "symmetric — consistent with deliberate bleed" below threshold. Safe-zone
recall fell 43% → 14%: a confident all-clear from a signal that misses a third of real
intrusions, and the model believed it. I had already written the guard against this into
the text detector one module over. **A weak signal with confident framing is worse than no
signal.**

**The metric could not express its own target.** SC-002 is ≤1%. With 46 approvals one
error scores 2.2%, so the observable values were 0% or ≥2.2%. I had been tuning against
quantisation noise for several rounds before spotting it.

## 5. What to say when you don't know

Point at `limits.md`. It is ten sections of things this system cannot do, most of them
found by measurement. Saying *"that's section 4, the stroke-width quantisation band — a
legitimate 0.55pt line gets rejected at 300 DPI and I chose that over loosening the
threshold"* is a stronger answer than a confident guess.

The one thing not to do is claim a number the eval cannot support. Every figure in
`results.md` has a run behind it in `capstone/evals/runs/`.

## 6. A 45-minute self-test

Answer out loud, without notes. Check yourself against the files afterwards.

1. Draw the verdict flow from upload to one of three outcomes.
2. Name the five conditions `APPROVE` has to satisfy in `finalize()`.
3. Why does `check_aspect` compare against the canvas ratio rather than the trim ratio?
4. Why is `ASPECT_MISMATCH` suppressed when `MISSING_BLEED` fires?
5. What does `magnitude = 1.1` mean in a gold label, and why is the direction uniform?
6. Why does the text detector escalate when it finds nothing?
7. What would you build next, and why that rather than something else?

Number 7 has a right answer: **the red team.** SC-006 has no measurement, injection via
rendered image text is the attack this design invites, and it is the only success
criterion with nothing behind it.
