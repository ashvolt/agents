# Data Model: Artwork Preflight Triage

Every entity below becomes a Pydantic v2 model in `capstone/src/schemas.py`. Validation
happens at every boundary — especially on model output, per FR-011.

**Date:** 2026-09-22

---

## Enumerations

### `IssueCode`

| Code | Bucket | Detected by |
|---|---|---|
| `LOW_RESOLUTION` | 1 | effective DPI at ordered size < spec minimum |
| `MISSING_BLEED` | 1 | artwork extent < trim + required bleed |
| `WRONG_COLOR_MODE` | 1 | mode not in spec's accepted set |
| `ASPECT_MISMATCH` | 1 | aspect ratio deviates beyond tolerance |
| `UNREADABLE_FILE` | 1 | decode failed, empty, or unsupported format |
| `THIN_LINES` | 2 | minimum stroke width < spec minimum |
| `LOW_CONTRAST` | 2 | ΔE between adjacent regions < spec minimum |
| `UNINTENDED_TRANSPARENCY` | 2 | alpha present where spec disallows |
| `TEXT_TOO_SMALL` | 2 | detected text height in pt < spec minimum |
| `CONTENT_IN_SAFE_ZONE` | 3 | meaningful content inside the cut margin |
| `LOOKS_WRONG` | 3 | unstructured gestalt concern |

### `Severity`
`BLOCKING` — cannot print. `ADVISORY` — printable, customer should know.

### `VerdictType`
`APPROVE` | `REQUEST_FIX` | `ESCALATE`

### `EscalationReason`
`LOW_CONFIDENCE` | `JUDGEMENT_WITHOUT_CORROBORATION` | `SIGNALS_DISAGREE` |
`UNSUPPORTED_INPUT` | `BUDGET_EXHAUSTED` | `MODEL_ERROR` | `SCHEMA_INVALID` |
`TRUNCATED_RESPONSE` | `REFUSAL` | `DEGRADED_MODE` | `INTERNAL_ERROR`

Every non-`APPROVE` outcome that is not a clean `REQUEST_FIX` names one of these. There is
no unlabelled escalation.

---

## Entities

### `ProductSpec`

Per-product print requirements. Looked up, never hardcoded into a check (FR-009).

| Field | Type | Meaning |
|---|---|---|
| `product_id` | `str` | e.g. `die-cut-sticker` |
| `display_name` | `str` | human label |
| `min_dpi` | `int` | minimum effective DPI at ordered size |
| `bleed_in` | `float` | required bleed, inches per edge |
| `safe_zone_in` | `float` | keep-out margin inside the trim |
| `min_text_pt` | `float` | smallest text that survives the press |
| `min_stroke_pt` | `float` | thinnest printable stroke |
| `min_contrast_delta_e` | `float` | minimum ΔE between adjacent regions |
| `accepted_color_modes` | `tuple[str, ...]` | e.g. `("CMYK",)` |
| `allows_transparency` | `bool` | whether alpha is meaningful for this product |
| `aspect_tolerance` | `float` | fractional deviation allowed |

**Invariant:** unknown `product_id` raises. It never falls back to a default — a guessed
spec is a silent false approve waiting to happen.

### `OrderMetadata`

| Field | Type | Meaning |
|---|---|---|
| `order_id` | `str` | idempotency key (FR-014) |
| `product_id` | `str` | joins to `ProductSpec` |
| `width_in` | `float` | ordered width, inches |
| `height_in` | `float` | ordered height, inches |
| `quantity` | `int` | ≥ 1 |

### `PreflightCase`

One unit of work: a file plus its order.

| Field | Type |
|---|---|
| `case_id` | `str` |
| `image_path` | `Path` |
| `order` | `OrderMetadata` |

### `Evidence`

Why an issue was reported. FR-008 requires this on every issue — vague advice is a defect.

| Field | Type | Meaning |
|---|---|---|
| `measured` | `float \| None` | what was measured |
| `required` | `float \| None` | what the spec demands |
| `unit` | `str \| None` | `dpi`, `pt`, `in`, `deltaE`, `ratio` |
| `region` | `tuple[int,int,int,int] \| None` | pixel bbox, when located |
| `note` | `str \| None` | free text, for bucket 3 only |

**Invariant:** at least one of `measured`/`region`/`note` is populated.

### `Issue`

| Field | Type |
|---|---|
| `code` | `IssueCode` |
| `severity` | `Severity` |
| `bucket` | `1 \| 2 \| 3` |
| `message` | `str` — human-readable, customer-safe |
| `evidence` | `Evidence` |

### `Verdict`

The output contract. This is what the model is asked to produce for bucket 3 and what
`finalize()` returns for the run.

| Field | Type | Notes |
|---|---|---|
| `verdict` | `VerdictType` | |
| `issues` | `list[Issue]` | empty iff `APPROVE` |
| `confidence` | `float` | 0.0–1.0 |
| `customer_message` | `str \| None` | present only for `REQUEST_FIX` |
| `escalation_reason` | `EscalationReason \| None` | required iff `ESCALATE` |
| `checks_completed` | `list[str]` | which checks actually ran |
| `degraded` | `bool` | true if any subsystem was unavailable |

**Model validators (the Principle IV guardrails, enforced by the schema itself):**

1. `verdict == APPROVE` ⟹ `issues == []` **and** `escalation_reason is None` **and**
   `degraded is False`.
2. `verdict == ESCALATE` ⟹ `escalation_reason is not None`.
3. `verdict == REQUEST_FIX` ⟹ at least one `BLOCKING` issue **and** `customer_message`
   is non-empty.
4. `customer_message is not None` ⟹ `verdict == REQUEST_FIX`.

A model response that violates any of these fails validation, and a validation failure is
itself an escalation (`SCHEMA_INVALID`). The schema cannot be talked into approving.

### `GoldLabel`

Ground truth for a generated case. Correct by construction (FR-018).

| Field | Type | Meaning |
|---|---|---|
| `case_id` | `str` | |
| `is_clean` | `bool` | no defect injected |
| `injected` | `list[InjectedDefect]` | what the generator did |
| `expected_verdict` | `VerdictType` | `APPROVE` if clean, else not-`APPROVE` |
| `split` | `"train" \| "holdout"` | sealed at generation |

### `InjectedDefect`

| Field | Type | Meaning |
|---|---|---|
| `code` | `IssueCode` | which defect |
| `magnitude` | `float` | multiple of the spec threshold (0.5, 0.9, 1.1, 2.0) |
| `borderline` | `bool` | true when `0.9 ≤ magnitude ≤ 1.1` |

`magnitude` is what makes FR-019 auditable: a generator that only ever emits `0.5` and
`2.0` is visible in the data, and borderline recall can be reported separately from the
easy cases.

### `Trace`

One run, fully accounted (FR-015, FR-016).

| Field | Type |
|---|---|
| `case_id`, `order_id` | `str` |
| `started_at`, `ended_at` | `datetime` |
| `steps` | `list[TraceStep]` |
| `input_tokens`, `output_tokens` | `int` |
| `cache_read_tokens`, `cache_write_tokens` | `int` |
| `cost_usd` | `float` |
| `latency_ms` | `int` |
| `model` | `str` — as reported by the API, not as requested |
| `verdict` | `VerdictType` |
| `termination` | `str` — `completed` \| `budget_*` \| `error` |
| `injection_suspected` | `bool` |

### `TraceStep`

| Field | Type |
|---|---|
| `index` | `int` |
| `kind` | `"tool_call" \| "model_turn" \| "validation"` |
| `name` | `str` |
| `duration_ms` | `int` |
| `ok` | `bool` |
| `detail` | `dict` |

### `RunResult` / `SweepReport`

`RunResult` pairs one `Verdict` with its `GoldLabel` and `Trace`. `SweepReport` aggregates:

| Metric | Definition |
|---|---|
| `auto_approve_rate` | approved clean / total clean |
| `false_approve_rate` | approved defective / total approved |
| `escalation_rate` | escalated / total |
| `per_issue_recall` | per `IssueCode`, detected / injected |
| `borderline_recall` | recall restricted to `borderline == True` |
| `cost_per_file_usd` | mean |
| `p95_latency_ms` | |
| `cache_hit_rate` | cache_read / (cache_read + input) |
| `crash_count` | must be 0 (SC-007) |

`false_approve_rate` uses **approved defective / total approved** — the precision
denominator — because SC-002 is a statement about how trustworthy an approval is, not about
how many defective files exist.

---

## Relationships

```
ProductSpec ──lookup by product_id── OrderMetadata ──1:1── PreflightCase
                                                              │
                                     GoldLabel ──1:1──────────┤
                                                              │
                                          Verdict ◄──produces─┤
                                            │                 │
                                     Issue ─┘        Trace ◄──┘
                                       │
                                  Evidence
```
