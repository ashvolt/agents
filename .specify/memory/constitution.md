# Artwork Preflight Triage — Constitution

Non-negotiable principles for this project. Every spec, plan, and task is checked against
this file. A design that violates a principle is either redesigned or the violation is
recorded in the Complexity Tracking table of the relevant plan with a written
justification. Silent violations are defects.

> **Unofficial project.** Not affiliated with, endorsed by, or connected to Sticker Mule.
> No Sticker Mule data, systems, or assets are used. All data is synthetic and generated
> locally. Volume and cost figures are labelled assumptions.

---

## Core Principles

### I. Precision on APPROVE is the binding constraint

The agent's success metric is **auto-approval rate, subject to a false-approve rate at or
below 1%**. Never raw accuracy.

Any change that raises approval rate while pushing false approves past 1% is a
**regression** and the CI gate must fail it. Any change that lowers false approves at the
cost of approval rate is a trade to be measured, not assumed.

**Rationale:** A wrongly approved file becomes a bad print — reprint, reship, support
ticket, and damage to the thing the business competes on. At an assumed $18 all-in per
wrong approval and 2,100 auto-approvals/day, a 1% false-approve rate costs $378/day
against $653/day of savings. At 5% the project is worth less than nothing. The metric is
the ROI model, restated.

### II. Evals before implementation

The labelled dataset and the eval harness exist **before** the agent does. A baseline
number exists before any tuning. Nothing ships unless it beats the previous number.

Every addition — a tool, a loop, a pattern, a second model — earns its place against the
baseline or is removed. Rejected ideas are recorded with their numbers, not deleted.

**Rationale:** Without a baseline, "improvement" is an opinion. This ordering is the
single most common gap in agent portfolios and the most common cause of agents that feel
better and measure worse.

### III. Code where code is exact; the model where it is not

Checks are assigned to one of three buckets and the assignment is defended:

1. **Metadata** — exact from file headers. Python.
2. **Pixel analysis** — exact from pixels. Python + CPU detectors.
3. **Judgement** — not expressible as a threshold. The model.

A check may only sit in bucket 3 if it cannot be computed. The `--no-tools` control arm
measures the value of this split on every sweep rather than asserting it.

**Rationale:** Asking a language model to compute DPI is slower, costlier and less
accurate than four lines of Python. Asking Python whether a logo looks deliberately placed
does not work at all. Knowing which is which — and proving it with a control arm — is the
technical core of this project.

### IV. Fail toward ESCALATE, never toward APPROVE

Every failure path terminates in `ESCALATE`: a crash, a timeout, a malformed model
response, a blown budget, an unsupported file type, a tool error, a refusal, a truncated
generation.

`APPROVE` is reachable only by an affirmative path in which every check ran and passed.
There is no default-approve, no approve-on-exception, no approve-on-timeout.

**Rationale:** The cost asymmetry between failure modes is roughly 10:1. An agent that is
eager to escalate loses money slowly and recoverably; an agent that approves under
uncertainty loses money fast and irrecoverably.

### V. Customer files are untrusted input

The uploaded file is attacker-controlled. Text rendered inside an image is **data to be
described**, never an instruction. The system prompt and tool definitions are the only
instruction channel.

Files are never executed, never used to build code paths, never interpolated into system
context. Model output is validated against a schema before any side effect fires. The
red-team suite tests injection via image content specifically.

**Rationale:** This is a case where the attacker chooses the pixels. Prompt injection via
rendered text is the obvious attack and the one an auditor will ask about first.

### VI. Cost and latency are first-class metrics

Every run records tokens, latency, and dollars. Budgets are hard caps on steps, tokens,
wall-clock, and cost per file. Behaviour at the cap is designed, not accidental — and per
Principle IV, it is `ESCALATE`.

Cost per *completed task* is the measure, not cost per request. A cheaper call that needs
three retries is not cheaper.

**Rationale:** The API is stateless, so every tool-loop turn resends the whole
conversation. Input tokens grow each turn and cost is quadratic in turn count. An
architecture decision that looks free at one call is not free at eight.

### VII. Honest artifacts

Documented limits are a deliverable, not an appendix. Every document states what the
system cannot do and where it is known to fail. Rejected alternatives are recorded with
the arithmetic that rejected them. Assumptions are labelled as assumptions inline.

Commit history is not tidied. A commit that says "eval broken, do not trust these numbers"
is worth more than a clean lie.

**Rationale:** A portfolio piece that hides its limitations is worth less than one that
names them, and an engineer who records a rejected option with numbers is demonstrably
more useful than one who never considered it.

---

## Engineering Constraints

- **Python 3.13.** `anthropic` SDK used directly. No agent framework unless one earns its
  place against the baseline and the reason is written down.
- **`pydantic`** for every boundary schema. **`pytest`** for tests. **`ruff`** for lint.
- **Secrets** come from `.env` via `os.environ` only. Never a literal, never in a test
  fixture, never in a committed output cell. A leaked key is rotated, not rebased away.
- **Tests that hit the API** are marked `@pytest.mark.integration` and are deselected by
  default. Pure logic must be testable without network or spend.
- **Models:** `claude-haiku-4-5` for iteration, `claude-opus-5` for graded runs and final
  sweeps. Model ID comes from env, never hardcoded in a module.
- **Prompt caching is load-bearing, not an optimisation.** The system prompt and tool list
  stay byte-stable. `usage.cache_read_input_tokens` at zero across repeated calls is a
  defect, and the harness reports it.

## Development Workflow

1. Spec before plan. Plan before tasks. Tasks before code.
2. Every task names the file it touches and the test that proves it.
3. Deterministic logic gets a unit test that runs offline.
4. Any change to agent behaviour gets a sweep. The number goes in the commit message.
5. A red CI build caused by a quality regression is a success of the system, not a failure.

## Governance

This constitution supersedes convenience. Amendments require a dated entry in the
Amendment Log below stating what changed and why.

Complexity that violates a principle must be justified in the plan's Complexity Tracking
table, naming the simpler alternative that was rejected and why it was insufficient.

### Amendment Log

| Date | Version | Change |
|---|---|---|
| 2026-09-22 | 1.0.0 | Initial ratification. Principles I–VII derived from `capstone/docs/brief.md` §5–§9 and `PLAN.md` §6. |

**Version:** 1.0.0 | **Ratified:** 2026-09-22 | **Last amended:** 2026-09-22
