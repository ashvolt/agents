# Runbook — Artwork Preflight Triage

How to run it, what breaks, what to do about it.

> **Status: partial.** The alerting and deployment sections describe the intended
> operational model; they are not wired up. Sections 1-4 are live and reproducible today.
> Sections 5-7 are design, marked as such. See [limits.md](limits.md) §7.

---

## 1. Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
pip install -e ".[dev]"

cp .env.example .env                # then put a real key in it
```

`.env` needs:

```
ANTHROPIC_API_KEY=sk-ant-...        # ~100+ chars. A short value silently 401s every call.
CHEAP_MODEL=claude-haiku-4-5        # iteration
DEFAULT_MODEL=claude-opus-5         # graded sweeps
```

Verify before anything else:

```bash
python -c "import anthropic; anthropic.Anthropic().messages.create(model='claude-haiku-4-5', max_tokens=8, messages=[{'role':'user','content':'hi'}]); print('auth OK')"
```

If this fails with `authentication_error`, stop. Every downstream number will be a
degradation-path artefact, not a measurement.

## 2. Generate the dataset

```bash
python -m capstone.data.generate -n 200
```

Writes `capstone/data/cases.jsonl` plus images under `capstone/data/cases/` (gitignored).
Deterministic for a given `--seed`. The holdout split is assigned at generation time.

## 3. Run an eval

```bash
python -m capstone.evals.harness rules_only            # deterministic baseline
python -m capstone.evals.harness always_escalate       # floor
python -m capstone.evals.harness always_approve        # ceiling; should BREACH
python -m capstone.evals.harness agent --save          # costs money
python -m capstone.evals.harness agent --no-tools      # control arm
```

Exit code is **0 only when the sweep passes** both SC-001 and SC-002 with zero crashes.
That is what makes it usable as a CI gate.

**The holdout is not readable by accident.** `--split holdout` exists and prints a warning.
Per T110 it is scored exactly once, at the end of the project.

## 4. Tests and lint

```bash
pytest capstone/tests -q          # 80 tests, offline, free
ruff check capstone/
```

No test in `capstone/tests/` touches the API. If one ever needs to, it gets
`@pytest.mark.integration` and is deselected by default.

## 5. Failure modes and responses  *(design — not yet alerting)*

| Symptom | Cause | Response |
|---|---|---|
| Every verdict is `ESCALATE` with `MODEL_ERROR` | API auth, network, or outage | Check key. Deterministic checks still run; the queue fills but nothing is wrongly approved. Safe to leave running. |
| `ESCALATE` / `BUDGET_EXHAUSTED` rising | Model looping on tool calls, or budgets set too tight | Inspect traces for repeated tool names. Raise `DEFAULT_MAX_STEPS` only after confirming the loop terminates. |
| `ESCALATE` / `SCHEMA_INVALID` rising | Model stopped calling `submit_verdict`, or prompt drift | Check a trace's final `model_turn`. One repair attempt is automatic; a sustained rise means the prompt or tool schema changed. |
| `cache_hit_rate` at 0 across a sweep | Silent cache invalidator | Something varying entered the prefix — a timestamp, a per-case string in the system prompt, a reordered tool list. Diff `prompts.py` and `registry.py`. |
| `cost_per_file` climbing | More loop turns per file | Input tokens grow each turn, so cost is quadratic in turn count. Look at step counts before looking at pricing. |
| Crash count above 0 | SC-007 violation | The harness records the traceback in the run JSON. This is a bug, not an operational event. |
| False-approve rate above 1% | **Stop.** | Roll back to the last passing revision. This is the only metric that justifies taking the system offline. |

## 6. Rollback  *(design)*

The agent is stateless per file and idempotent by `order_id`, so rollback is a revision
change with no data migration. Procedure:

1. Revert to the last commit whose message carries a passing sweep number.
2. Re-run `python -m capstone.evals.harness agent` and confirm the number matches.
3. Files processed by the bad revision that were **approved** need re-checking; files
   escalated or sent for fix do not, because a human already has them.

That asymmetry is the design paying off: only the autonomous verdict needs undoing.

## 7. On-call story  *(design)*

**What pages:** false-approve rate above 1% on the rolling sweep. Nothing else.

**What does not page:** escalation rate spikes, model outages, budget exhaustion. All of
those degrade toward a human, which is the designed safe state — the queue gets longer
and the artist does more work, which is exactly the status quo before this system existed.

**The worst realistic incident** is a prompt or model change that quietly raises approvals
while raising false approves. It does not announce itself: throughput improves and the
queue shrinks, which looks like a win on every dashboard except the one that matters.
This is why the CI gate scores the eval on every change rather than trusting review, and
why SC-002 is a constraint rather than a metric to be traded.

## 8. What operating this actually costs

At the assumed 4,000 files/day, from [limits.md](limits.md) §6: $0.03/file at list price
on Haiku, against a $0.01 target. Until caching and batch are measured, budget for
**$120/day**, not $40. That is a real number to give a manager, and it is three times the
one in the brief.
