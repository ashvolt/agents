"""Baselines. These exist before the agent, per Constitution Principle II.

Three arms, each answering a different question:

- `always_escalate` — the floor. Proves the metric moves and that a system can score
  perfectly on SC-002 while being worth nothing, which is exactly why SC-001 exists
  alongside it.
- `always_approve` — the ceiling on approve rate and the worst possible SC-002. Included
  because a metric you cannot break is a metric you cannot trust.
- `rules_only` — buckets 1 and 2, no model at all. **This is the number the agent must
  beat.** If the agent cannot beat deterministic Python, the agent is not earning its
  cost and the honest conclusion is to ship the Python.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PIL import Image

from capstone.ops.tracing import attach_trace
from capstone.src.product_specs import UnknownProductError, get_spec
from capstone.src.schemas import (
    EscalationReason,
    Evidence,
    Issue,
    IssueCode,
    PreflightCase,
    Severity,
    Trace,
    Verdict,
    VerdictType,
)
from capstone.tools.bucket1_metadata import effective_dpi, inspect_file
from capstone.tools.bucket2_pixels import analyse_pixels


def always_escalate(case: PreflightCase) -> Verdict:
    """Send everything to a human. Zero false approves, zero value."""
    return Verdict(
        verdict=VerdictType.ESCALATE,
        confidence=0.0,
        escalation_reason=EscalationReason.LOW_CONFIDENCE,
        checks_completed=[],
    )


def always_approve(case: PreflightCase) -> Verdict:
    """Approve everything. Maximum approve rate, catastrophic SC-002.

    Here so the gate can be shown to fail. A constraint that has never been breached in
    testing is a constraint nobody has verified.
    """
    return Verdict(verdict=VerdictType.APPROVE, confidence=1.0, checks_completed=["none"])


def _customer_message(issues: list[Issue], case: PreflightCase) -> str:
    """Plain-language fix request citing the measurement for each blocking issue.

    FR-008: concrete evidence, never vague advice. Telling a customer to "improve the
    resolution" without saying what it is and what it needs to be is the second-ranked
    failure mode in brief.md S6.
    """
    lines = [
        f"We reviewed the artwork for order {case.order.order_id} and found "
        f"{len(issues)} item{'s' if len(issues) != 1 else ''} to fix before we print:",
        "",
    ]
    for issue in issues:
        lines.append(f"- {issue.message}")
        detail = issue.evidence.describe()
        if detail:
            lines.append(f"  ({detail})")
    lines += [
        "",
        "Reply with an updated file and we'll re-check it right away.",
    ]
    return "\n".join(lines)


def rules_only(case: PreflightCase) -> Verdict:
    """Deterministic checks only — buckets 1 and 2. No model, no network, ~free.

    The `CONTENT_IN_SAFE_ZONE` and gestalt checks cannot run here, so this arm is
    deliberately blind to bucket 3. Its false-approve rate on safe-zone cases is the
    precise gap the vision pass has to close, and the difference between this arm and the
    agent is the measured value of adding a model at all.
    """
    started = datetime.now(UTC)
    trace = Trace(
        case_id=case.case_id,
        order_id=case.order.order_id,
        started_at=started,
        model=None,
        termination="completed",
    )

    try:
        spec = get_spec(case.order.product_id)
    except UnknownProductError:
        attach_trace(trace)
        return Verdict(
            verdict=VerdictType.ESCALATE,
            confidence=0.0,
            escalation_reason=EscalationReason.UNSUPPORTED_INPUT,
            checks_completed=[],
        )

    checks: list[str] = []
    issues, meta = inspect_file(case.image_path, spec, case.order)
    checks.append("bucket1_metadata")

    if not meta.ok:
        attach_trace(trace)
        return Verdict(
            verdict=VerdictType.ESCALATE,
            confidence=1.0,
            issues=issues,
            escalation_reason=EscalationReason.UNSUPPORTED_INPUT,
            checks_completed=checks,
        )

    dpi = effective_dpi(meta, spec, case.order)
    if dpi:
        try:
            with Image.open(case.image_path) as img:
                img.load()
                pixel_issues, _boxes = analyse_pixels(img, spec, dpi)
            issues = issues + pixel_issues
            checks.append("bucket2_pixels")
        except OSError as exc:
            issues = issues + [
                Issue(
                    code=IssueCode.UNREADABLE_FILE,
                    severity=Severity.BLOCKING,
                    message="The artwork could not be fully decoded for pixel analysis.",
                    evidence=Evidence(note=f"{type(exc).__name__}: {exc}"),
                )
            ]

    attach_trace(trace)

    blocking = [i for i in issues if i.severity is Severity.BLOCKING]
    if blocking:
        return Verdict(
            verdict=VerdictType.REQUEST_FIX,
            confidence=0.95,
            issues=issues,
            customer_message=_customer_message(blocking, case),
            checks_completed=checks,
        )

    if issues:  # advisory only — a human should look, but nothing blocks the press
        return Verdict(
            verdict=VerdictType.ESCALATE,
            confidence=0.6,
            issues=issues,
            escalation_reason=EscalationReason.JUDGEMENT_WITHOUT_CORROBORATION,
            checks_completed=checks,
        )

    return Verdict(verdict=VerdictType.APPROVE, confidence=0.9, checks_completed=checks)


__all__ = ["always_approve", "always_escalate", "rules_only"]
