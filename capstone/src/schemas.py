"""Boundary schemas for artwork preflight triage.

Every value that crosses a boundary — model output, tool result, dataset row, trace
record — is validated here. See specs/001-artwork-preflight-triage/data-model.md.

The `Verdict` validators are the load-bearing part of this module. Constitution
Principle IV says the system must fail toward ESCALATE and never toward APPROVE; the
validators make that a property of the type rather than a property of the control flow,
so a model response cannot talk its way into an approval that skipped checks.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class IssueCode(StrEnum):
    """Every defect the system can report, grouped by which bucket detects it."""

    # Bucket 1 — metadata, exact
    LOW_RESOLUTION = "LOW_RESOLUTION"
    MISSING_BLEED = "MISSING_BLEED"
    WRONG_COLOR_MODE = "WRONG_COLOR_MODE"
    ASPECT_MISMATCH = "ASPECT_MISMATCH"
    UNREADABLE_FILE = "UNREADABLE_FILE"

    # Bucket 2 — pixel analysis, exact
    THIN_LINES = "THIN_LINES"
    LOW_CONTRAST = "LOW_CONTRAST"
    UNINTENDED_TRANSPARENCY = "UNINTENDED_TRANSPARENCY"
    TEXT_TOO_SMALL = "TEXT_TOO_SMALL"

    # Bucket 3 — judgement, the model
    CONTENT_IN_SAFE_ZONE = "CONTENT_IN_SAFE_ZONE"
    LOOKS_WRONG = "LOOKS_WRONG"


BUCKET_OF: dict[IssueCode, int] = {
    IssueCode.LOW_RESOLUTION: 1,
    IssueCode.MISSING_BLEED: 1,
    IssueCode.WRONG_COLOR_MODE: 1,
    IssueCode.ASPECT_MISMATCH: 1,
    IssueCode.UNREADABLE_FILE: 1,
    IssueCode.THIN_LINES: 2,
    IssueCode.LOW_CONTRAST: 2,
    IssueCode.UNINTENDED_TRANSPARENCY: 2,
    IssueCode.TEXT_TOO_SMALL: 2,
    IssueCode.CONTENT_IN_SAFE_ZONE: 3,
    IssueCode.LOOKS_WRONG: 3,
}

DETERMINISTIC_CODES = frozenset(c for c, b in BUCKET_OF.items() if b in (1, 2))
JUDGEMENT_CODES = frozenset(c for c, b in BUCKET_OF.items() if b == 3)


class Severity(StrEnum):
    BLOCKING = "BLOCKING"  # cannot print
    ADVISORY = "ADVISORY"  # printable, but the customer should know


class VerdictType(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_FIX = "REQUEST_FIX"
    ESCALATE = "ESCALATE"


class EscalationReason(StrEnum):
    """Why a human is being asked. There is no unlabelled escalation."""

    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    JUDGEMENT_WITHOUT_CORROBORATION = "JUDGEMENT_WITHOUT_CORROBORATION"
    SIGNALS_DISAGREE = "SIGNALS_DISAGREE"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MODEL_ERROR = "MODEL_ERROR"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    TRUNCATED_RESPONSE = "TRUNCATED_RESPONSE"
    REFUSAL = "REFUSAL"
    DEGRADED_MODE = "DEGRADED_MODE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Split(StrEnum):
    TRAIN = "train"
    HOLDOUT = "holdout"


# --------------------------------------------------------------------------------------
# Product requirements
# --------------------------------------------------------------------------------------


class ProductSpec(BaseModel):
    """Print requirements for one product.

    Looked up per order, never hardcoded into a check (FR-009). A missing spec raises
    rather than defaulting — a guessed spec is a silent false approve waiting to happen.
    """

    model_config = ConfigDict(frozen=True)

    product_id: str
    display_name: str
    min_dpi: int = Field(gt=0)
    bleed_in: float = Field(ge=0)
    safe_zone_in: float = Field(ge=0)
    min_text_pt: float = Field(gt=0)
    min_stroke_pt: float = Field(gt=0)
    min_contrast_delta_e: float = Field(gt=0)
    accepted_color_modes: tuple[str, ...]
    allows_transparency: bool
    aspect_tolerance: float = Field(gt=0, le=1.0)

    @model_validator(mode="after")
    def _at_least_one_color_mode(self) -> ProductSpec:
        if not self.accepted_color_modes:
            raise ValueError("accepted_color_modes must not be empty")
        return self


class OrderMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    width_in: float = Field(gt=0)
    height_in: float = Field(gt=0)
    quantity: int = Field(ge=1)

    @property
    def aspect_ratio(self) -> float:
        return self.width_in / self.height_in


class PreflightCase(BaseModel):
    """One unit of work: a file plus the order it belongs to."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    image_path: Path
    order: OrderMetadata


# --------------------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------------------


class Evidence(BaseModel):
    """Why an issue was reported.

    FR-008: every issue carries a concrete measurement or a located region. Vague advice
    is a defect — it produces the second-ranked failure mode in brief.md S6, where a
    customer is told to fix a non-problem.
    """

    model_config = ConfigDict(frozen=True)

    measured: float | None = None
    required: float | None = None
    unit: str | None = None
    region: tuple[int, int, int, int] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _must_carry_something(self) -> Evidence:
        if self.measured is None and self.region is None and not self.note:
            raise ValueError(
                "Evidence must carry at least one of: measured, region, note. "
                "An issue without evidence is not actionable."
            )
        return self

    def describe(self) -> str:
        """One line a human can read, for the escalation queue and customer message."""
        if self.measured is not None and self.required is not None:
            unit = f" {self.unit}" if self.unit else ""
            return f"measured {self.measured:g}{unit}, requires {self.required:g}{unit}"
        if self.region is not None:
            x0, y0, x1, y1 = self.region
            return f"region ({x0}, {y0}) to ({x1}, {y1})"
        return self.note or ""


class Issue(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: IssueCode
    severity: Severity
    message: str = Field(min_length=1)
    evidence: Evidence

    @property
    def bucket(self) -> int:
        return BUCKET_OF[self.code]

    @property
    def is_deterministic(self) -> bool:
        return self.code in DETERMINISTIC_CODES


# --------------------------------------------------------------------------------------
# The output contract
# --------------------------------------------------------------------------------------


class Verdict(BaseModel):
    """The system's answer for one file.

    The validators below are Constitution Principle IV expressed as a type. Between them
    they make these states unrepresentable:

      - APPROVE carrying issues
      - APPROVE produced in degraded mode
      - APPROVE with an escalation reason attached
      - ESCALATE without saying why
      - REQUEST_FIX with no blocking issue or no customer message
      - a customer message attached to anything other than REQUEST_FIX

    A model response that violates any of them fails validation, and a validation failure
    is itself an escalation (SCHEMA_INVALID). The schema cannot be talked into approving.
    """

    verdict: VerdictType
    issues: list[Issue] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    customer_message: str | None = None
    escalation_reason: EscalationReason | None = None
    checks_completed: list[str] = Field(default_factory=list)
    degraded: bool = False

    @model_validator(mode="after")
    def _approve_is_clean(self) -> Verdict:
        if self.verdict is not VerdictType.APPROVE:
            return self
        if self.issues:
            raise ValueError("APPROVE cannot carry issues")
        if self.escalation_reason is not None:
            raise ValueError("APPROVE cannot carry an escalation_reason")
        if self.degraded:
            raise ValueError(
                "APPROVE cannot be produced in degraded mode — a check that did not run "
                "is not a check that passed"
            )
        return self

    @model_validator(mode="after")
    def _escalate_says_why(self) -> Verdict:
        if self.verdict is VerdictType.ESCALATE and self.escalation_reason is None:
            raise ValueError("ESCALATE must name an escalation_reason")
        return self

    @model_validator(mode="after")
    def _request_fix_is_actionable(self) -> Verdict:
        if self.verdict is not VerdictType.REQUEST_FIX:
            return self
        if not any(i.severity is Severity.BLOCKING for i in self.issues):
            raise ValueError("REQUEST_FIX requires at least one BLOCKING issue")
        if not (self.customer_message or "").strip():
            raise ValueError("REQUEST_FIX requires a customer_message")
        return self

    @model_validator(mode="after")
    def _message_only_on_request_fix(self) -> Verdict:
        if self.customer_message is not None and self.verdict is not VerdictType.REQUEST_FIX:
            raise ValueError("customer_message is only valid on REQUEST_FIX")
        return self

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.BLOCKING]


# --------------------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------------------


class InjectedDefect(BaseModel):
    """What the generator did to a file, and how hard.

    `magnitude` is a multiple of the spec threshold. FR-019 requires defects straddling
    the limit rather than sitting at one comfortable value, and recording the multiple is
    what makes that auditable: a generator that only ever emits 0.5 and 2.0 is visible in
    the data, and borderline recall can be reported separately from the easy cases.
    """

    model_config = ConfigDict(frozen=True)

    code: IssueCode
    magnitude: float = Field(gt=0)

    @property
    def borderline(self) -> bool:
        return 0.9 <= self.magnitude <= 1.1


class GoldLabel(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    is_clean: bool
    injected: tuple[InjectedDefect, ...] = ()
    split: Split = Split.TRAIN

    @model_validator(mode="after")
    def _clean_means_no_defects(self) -> GoldLabel:
        if self.is_clean and self.injected:
            raise ValueError("a clean case cannot carry injected defects")
        if not self.is_clean and not self.injected:
            raise ValueError("a defective case must record what was injected")
        return self

    @property
    def expected_not_approve(self) -> bool:
        """True when approving this file would be a false approve."""
        return not self.is_clean

    @property
    def injected_codes(self) -> frozenset[IssueCode]:
        return frozenset(d.code for d in self.injected)

    @property
    def has_borderline(self) -> bool:
        return any(d.borderline for d in self.injected)


# --------------------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------------------


class TraceStep(BaseModel):
    index: int
    kind: Literal["tool_call", "model_turn", "validation"]
    name: str
    duration_ms: int = Field(ge=0)
    ok: bool = True
    detail: dict[str, Any] = Field(default_factory=dict)


class Trace(BaseModel):
    """One run, fully accounted (FR-015, FR-016).

    `cache_read_tokens` is here rather than in a nice-to-have metrics bag because
    Constitution Principle VI treats caching as load-bearing: zero cache reads across
    repeated calls means a silent invalidator, and that is a defect the harness must be
    able to surface.
    """

    case_id: str
    order_id: str
    started_at: datetime
    ended_at: datetime | None = None
    steps: list[TraceStep] = Field(default_factory=list)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    model: str | None = None
    verdict: VerdictType | None = None
    termination: str = "incomplete"
    injection_suspected: bool = False

    @property
    def cache_hit_rate(self) -> float:
        billed = self.input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / billed if billed else 0.0


class RunResult(BaseModel):
    """One case scored: what the system said, what the truth was, what it cost."""

    case_id: str
    verdict: Verdict
    label: GoldLabel
    trace: Trace

    @property
    def approved(self) -> bool:
        return self.verdict.verdict is VerdictType.APPROVE

    @property
    def is_false_approve(self) -> bool:
        """Approved a file that carried an injected defect. The failure that matters."""
        return self.approved and self.label.expected_not_approve

    @property
    def is_false_reject(self) -> bool:
        return not self.approved and self.label.is_clean

    @property
    def detected_codes(self) -> frozenset[IssueCode]:
        return frozenset(i.code for i in self.verdict.issues)


__all__ = [
    "BUCKET_OF",
    "DETERMINISTIC_CODES",
    "JUDGEMENT_CODES",
    "EscalationReason",
    "Evidence",
    "GoldLabel",
    "InjectedDefect",
    "Issue",
    "IssueCode",
    "OrderMetadata",
    "PreflightCase",
    "ProductSpec",
    "RunResult",
    "Severity",
    "Split",
    "Trace",
    "TraceStep",
    "Verdict",
    "VerdictType",
]
