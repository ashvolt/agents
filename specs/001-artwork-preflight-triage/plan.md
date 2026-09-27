# Implementation Plan: Artwork Preflight Triage

**Branch:** `001-artwork-preflight-triage` | **Date:** 2026-09-22 | **Spec:** [spec.md](spec.md)
**Constitution:** [v1.0.0](../../.specify/memory/constitution.md)

---

## 1. Summary

Triage customer-uploaded artwork into `APPROVE` / `REQUEST_FIX` / `ESCALATE` so that
obviously-clean files never reach a human reviewer, while anything uncertain does — with
evidence attached.

The technical approach is a **three-bucket check pipeline** driven by a Claude tool loop.
Buckets 1 and 2 are pure Python and run exactly; bucket 3 is a single vision call handling
only what cannot be computed. The eval harness is built first and every subsequent change
is scored against it.

## 2. Technical Context

| Dimension | Decision |
|---|---|
| **Language** | Python 3.13 |
| **Model access** | `anthropic` SDK used directly. No agent framework. |
| **Models** | `claude-haiku-4-5` iteration, `claude-opus-5` graded sweeps. From env, never hardcoded. |
| **Schemas** | `pydantic` v2 at every boundary |
| **Image work** | `Pillow` + `numpy` for generation and pixel analysis |
| **Text detection** | CPU detector, choice deferred (OQ-3). Interface defined now, implementation swappable. |
| **Testing** | `pytest`; API-touching tests marked `integration` and deselected by default |
| **Lint** | `ruff`, line length 100 |
| **Storage** | Filesystem. JSONL for cases, labels, traces, and run results. No database. |
| **Target scale** | 4,000 files/day assumed; the eval set is ~200 cases |
| **Perf goals** | p95 ≤ 20s/file, ≤ $0.01/file (OQ-1 open) |

## 3. Constitution Check

Run before Phase 0 and again after Phase 1.

| Principle | How this plan satisfies it | Status |
|---|---|---|
| I. Precision on APPROVE | `false_approve_rate` is a first-class harness metric; gate fails above 1% | PASS |
| II. Evals before implementation | Phase 1 (dataset) and Phase 2 (harness) precede Phase 4 (agent). A `--no-tools` and a rules-only baseline exist before the agent does. | PASS |
| III. Code vs model | Buckets are separate modules with separate tests. Control arm measures the split. | PASS |
| IV. Fail toward ESCALATE | Single `finalize()` chokepoint; every exception path routes through it. Unit-tested with injected failures. | PASS |
| V. Untrusted input | Image bytes never enter the system prompt. Vision content is wrapped in an explicit data framing. Output schema-validated. Red-team suite in Phase 7. | PASS |
| VI. Cost/latency first-class | `Trace` carries tokens, cost, latency, cache reads. Budgets enforced in the loop, not around it. | PASS |
| VII. Honest artifacts | `docs/limits.md` is a deliverable. Rejected options recorded in [research.md](research.md). | PASS |

**Initial gate: PASS.** No complexity deviations required.

## 4. Project Structure

```
capstone/
  src/
    __init__.py
    schemas.py          # Pydantic: Issue, Verdict, PreflightCase, ProductSpec, Trace
    product_specs.py    # the invented spec table + lookup
    agent.py            # the tool loop, budgets, finalize()
    prompts.py          # system prompt + vision framing (byte-stable for caching)
  tools/
    __init__.py
    bucket1_metadata.py # DPI, bleed, colour mode, aspect, readability
    bucket2_pixels.py   # stroke width, contrast, alpha, text size
    text_detect.py      # detector interface + a deterministic stub implementation
    registry.py         # tool JSON schemas + dispatch
    mcp_server.py       # same tools exposed over MCP (Phase 8)
  data/
    generate.py         # synthetic generator, straddled thresholds
    cases/              # generated PNGs (gitignored)
    cases.jsonl         # case metadata + gold labels
  evals/
    harness.py          # runner
    metrics.py          # the metric set
    gate.py             # CI regression gate
    runs/               # results (gitignored)
  ops/
    tracing.py          # Trace emit + JSONL sink
    budgets.py          # step/token/wallclock/cost caps
    hitl.py             # escalation queue
  tests/
    test_schemas.py
    test_bucket1.py
    test_bucket2.py
    test_generator.py
    test_harness.py
    test_agent_failure_paths.py
    test_injection.py
  docs/
    brief.md            # business case (exists)
    architecture.md     # engineering picture (exists)
    runbook.md          # operations
    limits.md           # documented failure boundaries
```

## 5. Phases

Phase order enforces Constitution Principle II. Each phase ends with something runnable.

### Phase 0 — Research and decisions
Resolve every technical unknown that affects the data model. Output: [research.md](research.md).
**Exit:** no `NEEDS CLARIFICATION` remains in the data model.

### Phase 1 — Schemas + product specs + synthetic dataset
The dataset is the foundation; labels correct by construction. Defects injected straddling
thresholds per FR-019.
**Exit:** ~200 cases on disk with gold labels, held-out split sealed, generator unit-tested.

### Phase 2 — Eval harness + metrics + baselines
Harness runs any `Callable[[PreflightCase], Verdict]`. Two baselines land before the agent:
`always_escalate` (floor — proves the metric works) and `rules_only` (buckets 1+2, no model).
**Exit:** `rules_only` has a number. Cost model measured, OQ-1 resolved.

### Phase 3 — Deterministic checks (buckets 1 and 2)
Pure functions, exact, offline-testable.
**Exit:** per-issue recall measured on the training split; all bucket tests offline-green.

### Phase 4 — The agent
Tool definitions, the loop, structured verdict, validation, repair path, `finalize()`.
**Exit:** first agent number; `--no-tools` control arm measured against it.

### Phase 5 — Hill-climb
Prompt and threshold tuning against the training split only. Every accepted and rejected
change recorded with its number.
**Exit:** SC-001 and SC-002 met on the training split.

### Phase 6 — Production pass
Tracing, budgets, HITL queue, idempotency, graceful degradation, runbook.
**Exit:** every failure mode in the spec has a test proving it lands on `ESCALATE`.

### Phase 7 — Red team
Injection via rendered image text, malformed responses, hostile metadata. Failure taxonomy.
**Exit:** SC-006 = 0 on the red-team suite.

### Phase 8 — MCP + CI gate
Tools exposed over MCP; the eval gate runs in CI and can go red.
**Exit:** a deliberately-bad change makes CI red.

### Phase 9 — Held-out scoring + portfolio
Score the sealed split **once**. Whatever it says is the headline number.
**Exit:** README, architecture doc, limits doc, demo.

## 6. Design Decisions

### 6.1 Why a tool loop and not a single call

The model decides *which* measurements it needs. A file failing `UNREADABLE_FILE` needs no
further checks; a file passing metadata needs pixel analysis; only a file passing both
justifies a vision call. That ordering is a cost decision the model can make per-file and a
fixed pipeline cannot.

### 6.2 Deterministic-first ordering

Buckets 1 and 2 run before any vision call. Two reasons: a file that already has a citable
defect does not need a judgement call, and the vision prompt can be given the measurements
as context, which sharply reduces hallucinated findings.

### 6.3 `finalize()` as the single verdict chokepoint

Every path — success, exception, budget exhaustion, schema failure, refusal, truncation —
returns through one function. `APPROVE` is reachable from exactly one branch of it, guarded
by an explicit "all checks ran and passed" predicate. This is Principle IV made structural
rather than aspirational.

### 6.4 Prompt caching is architectural

System prompt and tool list are frozen strings assembled in a fixed order; per-file data
goes after the last cache breakpoint. `cache_read_input_tokens == 0` across repeated calls
is reported by the harness as a defect.

### 6.5 Trust boundary

Image bytes go in a user-turn content block preceded by a fixed framing that names the
content as untrusted customer data. Nothing from the file is ever interpolated into the
system prompt. Filenames are sanitised before logging.

## 7. Complexity Tracking

No constitutional deviations. Three things deliberately *not* built, recorded so their
absence is a decision rather than an oversight:

| Not built | Why | Revisit when |
|---|---|---|
| Multi-agent | One agent plus deterministic tools is sufficient at this scope; splitting adds handoff failure modes and cost for no measured gain | Evals show a single agent cannot meet SC-001/SC-002 |
| Self-hosted vision model | Costs more than Haiku below ~30K files/day; calibration is worse and this design escalates on confidence | Volume > 30K files/day |
| Database | ~200 cases and per-file traces; JSONL is inspectable and diffable | Trace volume exceeds what a file scan can handle |

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Generator and checker share assumptions → circular grading | Evals look good, agent is untested | Straddled thresholds (FR-019); bucket 3 is graded against injections the checker never sees |
| $0.01/file unreachable (OQ-1) | ROI model invalid | Measure in Phase 2. Amend SC-003 or shrink token shape; record which. |
| Synthetic art far simpler than real uploads | Overstated performance | Documented in `limits.md` as the primary threat to external validity |
| Vision call hallucinated findings | False rejects, wasted artist time | Deterministic measurements passed as context; every issue must cite evidence |
| Held-out split peeked at | Reported number is meaningless | Split sealed at generation; scored once in Phase 9 |
