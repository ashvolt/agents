"""Schema invariants — the Principle IV guardrail.

The point of these tests is not that Pydantic works. It is that the states which would
let a defective file reach a press are *unrepresentable*, so no future refactor of the
control flow can reintroduce them by accident.

All offline. No API, no network, no spend.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from capstone.src.product_specs import UnknownProductError, all_product_ids, get_spec
from capstone.src.schemas import (
    BUCKET_OF,
    DETERMINISTIC_CODES,
    EscalationReason,
    Evidence,
    GoldLabel,
    InjectedDefect,
    Issue,
    IssueCode,
    OrderMetadata,
    Severity,
    Split,
    Verdict,
    VerdictType,
)


def blocking_issue(code: IssueCode = IssueCode.LOW_RESOLUTION) -> Issue:
    return Issue(
        code=code,
        severity=Severity.BLOCKING,
        message="Resolution is below the minimum for the ordered size.",
        evidence=Evidence(measured=96, required=150, unit="dpi"),
    )


# --------------------------------------------------------------------------------------
# APPROVE is the dangerous verdict. These tests guard it.
# --------------------------------------------------------------------------------------


def test_approve_with_no_issues_is_valid() -> None:
    v = Verdict(verdict=VerdictType.APPROVE, confidence=0.95, checks_completed=["dpi"])
    assert v.verdict is VerdictType.APPROVE
    assert v.issues == []


def test_approve_cannot_carry_issues() -> None:
    with pytest.raises(ValidationError, match="cannot carry issues"):
        Verdict(verdict=VerdictType.APPROVE, confidence=0.9, issues=[blocking_issue()])


def test_approve_cannot_carry_escalation_reason() -> None:
    with pytest.raises(ValidationError, match="escalation_reason"):
        Verdict(
            verdict=VerdictType.APPROVE,
            confidence=0.9,
            escalation_reason=EscalationReason.LOW_CONFIDENCE,
        )


def test_approve_cannot_be_produced_in_degraded_mode() -> None:
    # A check that did not run is not a check that passed. If the vision pass was
    # unavailable, the file has not been fully inspected and must not be approved.
    with pytest.raises(ValidationError, match="degraded"):
        Verdict(verdict=VerdictType.APPROVE, confidence=0.99, degraded=True)


def test_approve_cannot_carry_a_customer_message() -> None:
    with pytest.raises(ValidationError, match="only valid on REQUEST_FIX"):
        Verdict(verdict=VerdictType.APPROVE, confidence=0.9, customer_message="please fix")


# --------------------------------------------------------------------------------------
# ESCALATE must always say why — an escalation without reasoning is a slower human review
# --------------------------------------------------------------------------------------


def test_escalate_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="must name an escalation_reason"):
        Verdict(verdict=VerdictType.ESCALATE, confidence=0.2)


def test_escalate_with_reason_is_valid() -> None:
    v = Verdict(
        verdict=VerdictType.ESCALATE,
        confidence=0.2,
        escalation_reason=EscalationReason.LOW_CONFIDENCE,
    )
    assert v.escalation_reason is EscalationReason.LOW_CONFIDENCE


def test_escalate_may_carry_findings() -> None:
    v = Verdict(
        verdict=VerdictType.ESCALATE,
        confidence=0.4,
        issues=[blocking_issue(IssueCode.CONTENT_IN_SAFE_ZONE)],
        escalation_reason=EscalationReason.JUDGEMENT_WITHOUT_CORROBORATION,
    )
    assert len(v.issues) == 1


def test_escalate_is_valid_while_degraded() -> None:
    # The whole point of degraded mode: still produce a safe answer.
    v = Verdict(
        verdict=VerdictType.ESCALATE,
        confidence=0.0,
        escalation_reason=EscalationReason.DEGRADED_MODE,
        degraded=True,
    )
    assert v.degraded is True


@pytest.mark.parametrize("reason", list(EscalationReason))
def test_every_escalation_reason_is_usable(reason: EscalationReason) -> None:
    v = Verdict(verdict=VerdictType.ESCALATE, confidence=0.1, escalation_reason=reason)
    assert v.escalation_reason is reason


# --------------------------------------------------------------------------------------
# REQUEST_FIX must be actionable
# --------------------------------------------------------------------------------------


def test_request_fix_requires_a_blocking_issue() -> None:
    advisory = Issue(
        code=IssueCode.LOW_CONTRAST,
        severity=Severity.ADVISORY,
        message="Contrast is marginal.",
        evidence=Evidence(measured=14, required=15, unit="deltaE"),
    )
    with pytest.raises(ValidationError, match="BLOCKING"):
        Verdict(
            verdict=VerdictType.REQUEST_FIX,
            confidence=0.9,
            issues=[advisory],
            customer_message="Please raise the contrast.",
        )


def test_request_fix_requires_a_customer_message() -> None:
    with pytest.raises(ValidationError, match="customer_message"):
        Verdict(verdict=VerdictType.REQUEST_FIX, confidence=0.9, issues=[blocking_issue()])


def test_request_fix_rejects_a_whitespace_only_message() -> None:
    with pytest.raises(ValidationError, match="customer_message"):
        Verdict(
            verdict=VerdictType.REQUEST_FIX,
            confidence=0.9,
            issues=[blocking_issue()],
            customer_message="   \n  ",
        )


def test_request_fix_valid_shape() -> None:
    v = Verdict(
        verdict=VerdictType.REQUEST_FIX,
        confidence=0.92,
        issues=[blocking_issue()],
        customer_message="Your artwork is 96 DPI at the size ordered; we need 150 DPI.",
    )
    assert v.blocking_issues


# --------------------------------------------------------------------------------------
# Evidence — FR-008. An issue without evidence is not actionable.
# --------------------------------------------------------------------------------------


def test_empty_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one of"):
        Evidence()


def test_evidence_accepts_a_measurement() -> None:
    assert Evidence(measured=96, required=150, unit="dpi").describe() == (
        "measured 96 dpi, requires 150 dpi"
    )


def test_evidence_accepts_a_region() -> None:
    assert "region" in Evidence(region=(10, 10, 50, 50)).describe()


def test_evidence_accepts_a_note_for_judgement_findings() -> None:
    assert Evidence(note="logo overlaps the cut line").describe().startswith("logo")


# --------------------------------------------------------------------------------------
# Issue bucket routing — research.md D-1
# --------------------------------------------------------------------------------------


def test_every_issue_code_has_a_bucket() -> None:
    for code in IssueCode:
        assert code in BUCKET_OF, f"{code} has no bucket assignment"


def test_only_two_codes_reach_the_model() -> None:
    # D-1: the judgement bucket shrank from five checks to two. If this count grows,
    # the reassignment argument in research.md needs revisiting and the cost model with it.
    judgement = [c for c, b in BUCKET_OF.items() if b == 3]
    assert set(judgement) == {IssueCode.CONTENT_IN_SAFE_ZONE, IssueCode.LOOKS_WRONG}


def test_issue_reports_its_bucket() -> None:
    assert blocking_issue(IssueCode.THIN_LINES).bucket == 2
    assert blocking_issue(IssueCode.THIN_LINES).is_deterministic
    assert not blocking_issue(IssueCode.CONTENT_IN_SAFE_ZONE).is_deterministic


def test_deterministic_codes_are_the_nine_computable_ones() -> None:
    assert len(DETERMINISTIC_CODES) == 9


# --------------------------------------------------------------------------------------
# Gold labels — FR-018, FR-019
# --------------------------------------------------------------------------------------


def test_clean_label_cannot_carry_defects() -> None:
    with pytest.raises(ValidationError, match="clean case"):
        GoldLabel(
            case_id="c1",
            is_clean=True,
            injected=(InjectedDefect(code=IssueCode.THIN_LINES, magnitude=0.5),),
        )


def test_defective_label_must_record_what_was_injected() -> None:
    with pytest.raises(ValidationError, match="must record"):
        GoldLabel(case_id="c1", is_clean=False)


@pytest.mark.parametrize(
    ("magnitude", "expected"),
    [(0.5, False), (0.9, True), (1.0, True), (1.1, True), (2.0, False)],
)
def test_borderline_window_is_point_nine_to_one_point_one(
    magnitude: float, expected: bool
) -> None:
    # FR-019: borderline recall is reported separately, so the window must be explicit.
    assert InjectedDefect(code=IssueCode.LOW_RESOLUTION, magnitude=magnitude).borderline is expected


def test_label_exposes_injected_codes_and_borderline_flag() -> None:
    label = GoldLabel(
        case_id="c1",
        is_clean=False,
        injected=(
            InjectedDefect(code=IssueCode.LOW_RESOLUTION, magnitude=0.9),
            InjectedDefect(code=IssueCode.THIN_LINES, magnitude=2.0),
        ),
        split=Split.HOLDOUT,
    )
    assert label.injected_codes == {IssueCode.LOW_RESOLUTION, IssueCode.THIN_LINES}
    assert label.has_borderline
    assert label.expected_not_approve


# --------------------------------------------------------------------------------------
# Product specs — FR-009. A guessed spec is a silent false approve.
# --------------------------------------------------------------------------------------


def test_unknown_product_raises_rather_than_defaulting() -> None:
    with pytest.raises(UnknownProductError, match="no product spec"):
        get_spec("holographic-unobtainium")


def test_unknown_product_error_names_the_known_products() -> None:
    with pytest.raises(UnknownProductError, match="die-cut-sticker"):
        get_spec("nope")


@pytest.mark.parametrize("product_id", all_product_ids())
def test_every_spec_is_internally_consistent(product_id: str) -> None:
    spec = get_spec(product_id)
    assert spec.min_dpi > 0
    assert spec.accepted_color_modes
    assert spec.min_text_pt > spec.min_stroke_pt, (
        "text smaller than the thinnest printable stroke would be unreachable"
    )


def test_specs_are_frozen() -> None:
    spec = get_spec("die-cut-sticker")
    with pytest.raises(ValidationError):
        spec.min_dpi = 1  # type: ignore[misc]


def test_order_aspect_ratio() -> None:
    order = OrderMetadata(
        order_id="o1", product_id="die-cut-sticker", width_in=4, height_in=2, quantity=100
    )
    assert order.aspect_ratio == 2.0
