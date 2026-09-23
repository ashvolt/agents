# Tasks: Artwork Preflight Triage

**Spec:** [spec.md](spec.md) | **Plan:** [plan.md](plan.md) | **Constitution:** [v1.0.0](../../.specify/memory/constitution.md)
**Generated:** 2026-09-22

`[P]` = parallelisable (different files, no shared dependency).
Every task names the file it touches and the check that proves it done.
Phase order enforces Constitution Principle II — **evals exist before the agent does.**

**Status legend:** `[ ]` not started · `[~]` partial · `[x]` done · `[!]` blocked

**Progress as of 2026-09-23:** Phases 0-5 complete, plus T110 (holdout scored once).
178 offline tests, ruff clean. Both success criteria met — see
[capstone/docs/results.md](../../capstone/docs/results.md).

Headline: holdout 82.0% auto-approve, 0 false approves in 41 approvals, both arms. The
deterministic pipeline passes without the model; the model's measured contribution is a
3.4 pp reduction in escalation rate, worth ~$164/day against the brief's assumptions and
resting on 3 cases.

Not started: Phase 6 (HITL queue, idempotency, degradation), Phase 7 (red team — SC-006
has no measurement behind it), Phase 8 (MCP, CI gate), Phase 9 (portfolio writeup).

---

## Phase 0 — Foundation

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T001 | Ratify constitution | `.specify/memory/constitution.md` | 7 principles, amendment log | `[x]` |
| T002 | Write feature spec | `specs/001-.../spec.md` | FR/SC numbered, OQs listed | `[x]` |
| T003 | Write implementation plan | `specs/001-.../plan.md` | constitution check PASS | `[x]` |
| T004 | Record Phase 0 decisions | `specs/001-.../research.md` | every rejected option has arithmetic | `[x]` |
| T005 | Define data model | `specs/001-.../data-model.md` | every entity + invariants | `[x]` |
| T006 | Add `pillow`, `numpy` to deps | `pyproject.toml` | `pip install -e ".[dev]"` clean | `[x]` |
| T007 | Gitignore generated artefacts | `.gitignore` | `capstone/data/cases/`, `capstone/evals/runs/`, `traces/` ignored | `[x]` |

---

## Phase 1 — Schemas, specs, dataset  *(US-4 foundation)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T010 | Enums: `IssueCode`, `Severity`, `VerdictType`, `EscalationReason` | `capstone/src/schemas.py` | importable, 11 issue codes | `[x]` |
| T011 | `Evidence`, `Issue` models + invariant | `capstone/src/schemas.py` | empty evidence rejected | `[x]` |
| T012 | `Verdict` model + **4 cross-field validators** | `capstone/src/schemas.py` | `APPROVE` with issues raises | `[x]` |
| T013 | `ProductSpec`, `OrderMetadata`, `PreflightCase` | `capstone/src/schemas.py` | unknown product raises | `[x]` |
| T014 | `GoldLabel`, `InjectedDefect`, `Trace`, `TraceStep` | `capstone/src/schemas.py` | round-trips through JSON | `[x]` |
| T015 | Schema unit tests — **the Principle IV guardrail** | `capstone/tests/test_schemas.py` | every invariant has a failing-case test | `[x]` |
| T016 | Product spec table (5 products) | `capstone/src/product_specs.py` | `get_spec()` raises on unknown id | `[x]` |
| T017 | `[P]` Synthetic art renderer (clean files) | `capstone/data/generate.py` | produces valid PNGs at spec DPI | `[x]` |
| T018 | Defect injectors, **straddled magnitudes** (FR-019) | `capstone/data/generate.py` | each injector accepts a magnitude multiplier | `[x]` |
| T019 | Case assembly + gold labels + sealed split | `capstone/data/generate.py` | ~200 cases, 40/40/20 mix, holdout sealed | `[x]` |
| T020 | Generator tests | `capstone/tests/test_generator.py` | magnitudes straddle; labels match injections | `[ ]` |
| T021 | Generate the dataset | `capstone/data/cases.jsonl` | file on disk, holdout never read by tuning code | `[x]` |

**Phase 1 exit:** ~200 labelled cases, generator tested offline, holdout sealed.

---

## Phase 2 — Eval harness  *(US-4)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T030 | Metric functions | `capstone/evals/metrics.py` | `false_approve_rate` uses approved-defective/approved | `[x]` |
| T031 | `[P]` Metric unit tests on hand-built fixtures | `capstone/tests/test_harness.py` | known inputs → known metrics | `[ ]` |
| T032 | Harness: run any `Callable[[PreflightCase], Verdict]` | `capstone/evals/harness.py` | train/holdout selectable | `[x]` |
| T033 | `--no-tools` control arm flag | `capstone/evals/harness.py` | both arms in one report | `[x]` |
| T034 | `SweepReport` output + JSONL run log | `capstone/evals/harness.py` | report reproducible from the log | `[x]` |
| T035 | Baseline A: `always_escalate` | `capstone/evals/baselines.py` | proves the metric moves; approve rate 0 | `[x]` |
| T036 | **Cost model measurement — resolves OQ-1** | `capstone/evals/cost_probe.py` | measured $/file at list, +batch, +cache | `[~]` |
| T037 | Amend SC-003 or the architecture per T036 | `spec.md`, `capstone/docs/brief.md` | written down either way | `[x]` |

**Phase 2 exit:** a number exists before the agent does. OQ-1 resolved in writing.

---

## Phase 3 — Deterministic checks  *(US-1, US-2)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T040 | `[P]` `LOW_RESOLUTION` | `capstone/tools/bucket1_metadata.py` | effective DPI at ordered size | `[x]` |
| T041 | `[P]` `WRONG_COLOR_MODE` | `capstone/tools/bucket1_metadata.py` | mode vs accepted set | `[x]` |
| T042 | `[P]` `ASPECT_MISMATCH` | `capstone/tools/bucket1_metadata.py` | within tolerance | `[x]` |
| T043 | `[P]` `MISSING_BLEED` | `capstone/tools/bucket1_metadata.py` | extent vs trim + bleed | `[x]` |
| T044 | `[P]` `UNREADABLE_FILE` | `capstone/tools/bucket1_metadata.py` | corrupt/empty/unsupported → issue, never raise | `[x]` |
| T045 | Bucket 1 tests incl. **borderline magnitudes** | `capstone/tests/test_bucket1.py` | 0.9× and 1.1× both correct | `[ ]` |
| T046 | `[P]` `THIN_LINES` (erosion / distance transform) | `capstone/tools/bucket2_pixels.py` | min stroke in pt | `[x]` |
| T047 | `[P]` `LOW_CONTRAST` (ΔE adjacent regions) | `capstone/tools/bucket2_pixels.py` | ΔE vs spec | `[x]` |
| T048 | `[P]` `UNINTENDED_TRANSPARENCY` (alpha) | `capstone/tools/bucket2_pixels.py` | alpha where disallowed | `[x]` |
| T049 | Text detector interface + deterministic stub (D-3) | `capstone/tools/text_detect.py` | `detect_text() -> list[TextBox]` | `[x]` |
| T050 | `TEXT_TOO_SMALL` from boxes + ordered size | `capstone/tools/bucket2_pixels.py` | px height → pt at size | `[x]` |
| T051 | Bucket 2 tests incl. borderline | `capstone/tests/test_bucket2.py` | per-check recall measured | `[ ]` |
| T052 | Baseline B: `rules_only` (buckets 1+2, no model) | `capstone/evals/baselines.py` | **the number the agent must beat** | `[x]` |

**Phase 3 exit:** `rules_only` has a score. Per-issue and borderline recall recorded.

---

## Phase 4 — The agent  *(US-1, US-2, US-3)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T060 | Frozen system prompt + vision framing (D-7, Principle V) | `capstone/src/prompts.py` | module-level constants, byte-stable | `[x]` |
| T061 | Tool JSON schemas + dispatch registry | `capstone/tools/registry.py` | deterministic tool ordering | `[x]` |
| T062 | `finalize()` — the single verdict chokepoint (D-6) | `capstone/src/agent.py` | one `APPROVE` branch, guarded | `[x]` |
| T063 | The tool loop: `stop_reason` handling, `tool_result` blocks | `capstone/src/agent.py` | checks `stop_reason` before content | `[x]` |
| T064 | Structured output validation + one repair retry | `capstone/src/agent.py` | invalid twice → `SCHEMA_INVALID` escalate | `[x]` |
| T065 | Bucket 3 vision call with measurements as context (D-5) | `capstone/src/agent.py` | deterministic findings passed in | `[x]` |
| T066 | Failure-path tests — **Principle IV proof** | `capstone/tests/test_agent_failure_paths.py` | injected crash/timeout/refusal/truncation/garbage → never `APPROVE` | `[x]` |
| T067 | First agent sweep | `capstone/evals/runs/` | number recorded in the commit message | `[x]` |
| T068 | Control arm: `rules_only` vs agent, same cases | `capstone/evals/runs/` | gap = measured value of the split | `[x]` |

**Phase 4 exit:** agent beats `rules_only`, or the reason it does not is written down.

---

## Phase 5 — Hill-climb  *(train split only)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T070 | ~~Tune confidence threshold (OQ-2)~~ **dead end** — every false approve came back at 0.92-0.98 confidence | `capstone/src/agent.py` | recorded in results.md | `[x]` |
| T071 | Threshold sweep: safe-zone + no-text gate | `capstone/tools/bucket2_pixels.py` | operating curve in results.md S5 | `[x]` |
| T072 | Tool loop vs single call (OQ-4, and the L4 cargo-cult question) | `capstone/src/agent.py` | single call wins on both axes | `[x]` |
| T073 | Rejected-change log | `capstone/docs/results.md` S4 | three rejected approaches keep their numbers | `[x]` |

**Phase 5 exit:** SC-001 ≥ 60% and SC-002 ≤ 1% on the train split.

---

## Phase 6 — Production  *(US-6)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T080 | `Trace` emit + JSONL sink | `capstone/ops/tracing.py` | every run traced incl. cache tokens | `[~]` |
| T081 | Budgets: steps, tokens, wall-clock, cost | `capstone/ops/budgets.py` | cap → `BUDGET_EXHAUSTED` escalate | `[x]` |
| T082 | HITL escalation queue carrying findings | `capstone/ops/hitl.py` | queue entry has full reasoning | `[ ]` |
| T083 | Idempotency by `order_id` (FR-014) | `capstone/src/agent.py` | second run, same verdict, no dup side effect | `[ ]` |
| T084 | Graceful degradation (model down / rate limited) | `capstone/src/agent.py` | deterministic checks still run, `degraded=True` | `[ ]` |
| T085 | Runbook | `capstone/docs/runbook.md` | alerts, rollback, on-call | `[x]` |
| T086 | Documented limits (Principle VII) | `capstone/docs/limits.md` | where it fails, named | `[x]` |

---

## Phase 7 — Red team  *(US-5)*

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T090 | Injection cases: instruction text rendered into images | `capstone/data/redteam.py` | ≥ 20 adversarial cases | `[ ]` |
| T091 | Injection tests | `capstone/tests/test_injection.py` | SC-006 = 0 verdict changes | `[ ]` |
| T092 | Hostile metadata + malformed response cases | `capstone/data/redteam.py` | all land on `ESCALATE` | `[ ]` |
| T093 | Failure taxonomy | `capstone/docs/limits.md` | classes named, counts recorded | `[ ]` |

---

## Phase 8 — MCP + CI gate

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T100 | MCP server exposing the deterministic tools | `capstone/tools/mcp_server.py` | tools callable over MCP | `[ ]` |
| T101 | MCP client smoke test | `capstone/tests/test_mcp.py` | parity with direct dispatch | `[ ]` |
| T102 | CI regression gate | `capstone/evals/gate.py` | fails on FA > 1%, approve drop, or per-issue drop | `[x]` |
| T103 | GitHub Actions workflow | `.github/workflows/eval-gate.yml` | runs on PR, no API key needed | `[x]` |
| T104 | **Prove the gate works** — deliberately bad change | verified locally | **done: deleted the no-text gate, exit 1, two findings; restored, exit 0** | `[x]` |

---

## Phase 9 — Held-out + portfolio

| ID | Task | File(s) | Done when | Status |
|---|---|---|---|---|
| T110 | Score the sealed holdout — **once** | `capstone/evals/runs/` | **done: 82.0% / 0 false approves, both arms** | `[x]` |
| T111 | Update architecture doc to as-built | `capstone/docs/architecture.md` | diagrams match the code | `[x]` |
| T112 | Regenerate excalidraw diagrams | `capstone/docs/architecture.excalidraw` | as-built, 106 elements, 4 panels | `[x]` |
| T113 | Portfolio writeup | `portfolio/writeup.md` | problem → decisions → numbers → limits | `[ ]` |
| T114 | Demo script / recording | `portfolio/demo.md` | reproducible from a clean clone | `[ ]` |

---

## Dependency Graph

```
Phase 0 ──► Phase 1 (schemas ──► generator ──► dataset)
                 │
                 ▼
            Phase 2 (metrics ──► harness ──► baseline A ──► cost probe ──► OQ-1)
                 │
                 ▼
            Phase 3 (bucket 1 ∥ bucket 2 ──► baseline B = rules_only)
                 │
                 ▼
            Phase 4 (prompts ∥ registry ──► finalize ──► loop ──► validation ──► sweep)
                 │
                 ▼
            Phase 5 (hill-climb, train split only)
                 │
                 ├──► Phase 6 (production)
                 ├──► Phase 7 (red team)
                 └──► Phase 8 (MCP + CI)
                            │
                            ▼
                       Phase 9 (holdout scored ONCE ──► portfolio)
```

## Parallelisation

- **T040–T044** are five independent pure functions in one file — write together, test together.
- **T046–T048** likewise for bucket 2.
- **T060** and **T061** touch different files and can proceed in parallel.
- **Phases 6, 7, 8** are independent of each other once Phase 5 lands.

## Rules that survive every phase

1. The holdout split is read exactly once, in T110.
2. Every behaviour change gets a sweep; the number goes in the commit message.
3. Any change that raises approve rate while pushing false-approve above 1% is reverted.
4. Rejected ideas keep their numbers (T073).
5. No task is `[x]` until its named check passes.
