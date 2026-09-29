"""The customer register: the letter, and the rules the templates have to keep.

The interesting tests here are not the rendering ones. They are the three that encode why
the split exists at all:

- `test_no_template_advice_states_a_dpi_target` - a customer message that names a
  threshold invites the customer to satisfy the threshold. For resolution the easy way to
  do that is to resample the file, which passes `check_resolution` on the next upload and
  prints exactly as soft. That is a false approve caused by our own wording.
- `test_every_customer_facing_code_carries_advice` - a new blocking code must not quietly
  ship with the artist's sentence as its customer letter. That is the state this module
  was written to end.
- `test_advice_survives_the_tool_payload_round_trip` - the agent arm rebuilds issues from
  JSON, so advice that is not serialised is advice the agent arm silently loses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from capstone.src.customer_text import (
    ACTION_LABEL,
    AVOID_LABEL,
    TECHNICAL_LABEL,
    customer_letter,
    issue_lines,
)
from capstone.src.product_specs import get_spec
from capstone.src.schemas import (
    CustomerAdvice,
    Evidence,
    Issue,
    IssueCode,
    OrderMetadata,
    Severity,
)
from capstone.tools.bucket1_metadata import (
    FileMetadata,
    check_aspect,
    check_bleed,
    check_color_mode,
    check_readable,
    check_resolution,
)
from capstone.tools.bucket2_pixels import analyse_pixels
from capstone.tools.registry import _issues_payload, issues_from_payload

SPEC = get_spec("die-cut-sticker")
ORDER = OrderMetadata(
    order_id="A-1041", product_id="die-cut-sticker", width_in=5, height_in=3, quantity=1
)


def meta(**kw: object) -> FileMetadata:
    base: dict = {
        "mode": "CMYK",
        "width_px": 788,
        "height_px": 488,
        "declared_dpi": 150.0,
        "has_alpha": False,
    }
    return FileMetadata(Path("art.tif"), **{**base, **kw})  # type: ignore[arg-type]


def advice_of(issues: list[Issue], code: IssueCode) -> CustomerAdvice:
    found = next(i for i in issues if i.code is code)
    assert found.advice is not None, f"{code} carries no customer advice"
    return found.advice


# --------------------------------------------------------------------------------------
# The letter
# --------------------------------------------------------------------------------------


def test_letter_leads_with_the_consequence_and_ends_with_the_measurement() -> None:
    """Layering: what happens to their print first, the number last and labelled.

    The number is kept - a designer reading the letter can still act on it - but it no
    longer leads, and it is no longer the only thing present.
    """
    issue = Issue(
        code=IssueCode.LOW_RESOLUTION,
        severity=Severity.BLOCKING,
        message="Artwork is 138 DPI at the ordered size of 5x3 in.",
        evidence=Evidence(measured=137.9, required=150.0, unit="dpi"),
        advice=CustomerAdvice(
            headline="Your sticker will print a little softer than it should.",
            action="The image is 724 pixels wide; we need 788.",
            avoid="enlarging this copy",
        ),
    )
    lines = issue_lines(issue, 1)
    assert lines[0] == "1. Your sticker will print a little softer than it should."
    assert lines[1].strip().startswith(f"{ACTION_LABEL}:")
    assert lines[2].strip().startswith(f"{AVOID_LABEL}:")
    assert lines[3].strip() == f"{TECHNICAL_LABEL}: measured 137.9 dpi, requires 150 dpi."


def test_a_finding_with_no_advice_falls_back_to_the_artist_line() -> None:
    """A model-authored issue writes one string. The letter uses it rather than inventing
    an action the model did not give."""
    issue = Issue(
        code=IssueCode.LOOKS_WRONG,
        severity=Severity.BLOCKING,
        message="The logo appears to be a placeholder.",
        evidence=Evidence(note="text reads 'YOUR LOGO HERE'"),
    )
    lines = issue_lines(issue)
    assert lines[0] == "- The logo appears to be a placeholder."
    assert not any(ACTION_LABEL in line for line in lines)


def test_letter_reports_only_blocking_findings() -> None:
    """Advisories route a file to a person; they are not something to ask a customer."""
    blocking = Issue(
        code=IssueCode.MISSING_BLEED,
        severity=Severity.BLOCKING,
        message="No bleed.",
        evidence=Evidence(measured=0.0, required=0.125, unit="in"),
        advice=CustomerAdvice(headline="An edge could show.", action="Add 0.125 in."),
    )
    advisory = Issue(
        code=IssueCode.CONTENT_IN_SAFE_ZONE,
        severity=Severity.ADVISORY,
        message="A human should confirm.",
        evidence=Evidence(note="edge asymmetry"),
    )
    letter = customer_letter([blocking, advisory], order_id="A-1")
    assert "1 item to fix" in letter
    assert "A human should confirm" not in letter


def test_letter_counts_and_numbers_its_findings() -> None:
    issues = [
        Issue(
            code=code,
            severity=Severity.BLOCKING,
            message=f"{code} technical line.",
            evidence=Evidence(note="x"),
            advice=CustomerAdvice(headline=f"{code} consequence.", action="Do the thing."),
        )
        for code in (IssueCode.LOW_RESOLUTION, IssueCode.MISSING_BLEED)
    ]
    letter = customer_letter(issues, order_id="A-9")
    assert "order A-9 and found 2 items to fix" in letter
    assert "1. LOW_RESOLUTION consequence." in letter
    assert "2. MISSING_BLEED consequence." in letter


def test_letter_without_an_order_id_does_not_say_order_none() -> None:
    issue = Issue(
        code=IssueCode.THIN_LINES,
        severity=Severity.BLOCKING,
        message="Thin.",
        evidence=Evidence(measured=0.2, required=0.5, unit="pt"),
        advice=CustomerAdvice(headline="Lines may vanish.", action="Thicken them."),
    )
    letter = customer_letter([issue])
    assert "None" not in letter
    assert "1 item to fix" in letter


# --------------------------------------------------------------------------------------
# What the registers may and may not say
# --------------------------------------------------------------------------------------


# Every shipped customer-facing template, and every branch of one. The resolution advice
# branches three ways, and only branch 1 was listed here at first - so the guards below
# never saw branches 2 and 3, and a promise living in branch 2 went unnoticed until a
# mutation check found the hole rather than the test finding the bug.
CUSTOMER_FACING_ADVICE = [
    # branch 1: no DPI tag, pixels are the honest unit
    check_resolution(meta(declared_dpi=None, width_px=724, height_px=448), SPEC, ORDER),
    # branch 2: a DPI tag, pixels already sufficient
    check_resolution(meta(declared_dpi=72.0, width_px=3000, height_px=1857), SPEC, ORDER),
    # branch 3: a DPI tag, pixels genuinely short
    check_resolution(meta(declared_dpi=90.0, width_px=450, height_px=279), SPEC, ORDER),
    check_bleed(meta(width_px=750, height_px=450), SPEC, ORDER),
    check_aspect(meta(width_px=1200, height_px=488), SPEC, ORDER),
    check_color_mode(meta(mode="L"), SPEC),
    check_readable(
        meta(mode=None, width_px=0, height_px=0, declared_dpi=None, error="cannot identify")
    ),
]


def test_every_parametrized_group_actually_holds_a_finding() -> None:
    """The checks below loop over these groups, so an empty group is a test that passes
    without asserting anything. One of them was empty when this file was written."""
    assert all(group for group in CUSTOMER_FACING_ADVICE)


def states_a_bare_dpi_target(action: str) -> bool:
    """True when a customer action names a DPI figure without telling them to export.

    "needs at least 150 DPI" reads as an instruction to set the DPI to 150. That resamples
    the file: it adds pixels without detail, satisfies `check_resolution` on re-upload, and
    prints exactly as soft. A DPI figure is only safe in an action that says to *export* at
    that resolution for a stated print size, which is not a resample.
    """
    lowered = action.lower()
    if "dpi" not in lowered:
        return False
    return "export" not in lowered


def test_the_dpi_target_rule_catches_the_wording_it_is_meant_to_catch() -> None:
    """The rule above is the point of this module, so it is tested rather than trusted."""
    assert states_a_bare_dpi_target("Your file needs to be at least 150 DPI.")
    assert states_a_bare_dpi_target("Please set the resolution to 150 DPI.")
    assert not states_a_bare_dpi_target(
        "Export the artwork again at 150 DPI for a 5x3 in print."
    )
    assert not states_a_bare_dpi_target("The image is 724x448 pixels; we need 788x488.")


@pytest.mark.parametrize(
    "issues", CUSTOMER_FACING_ADVICE, ids=lambda i: str(i[0].code) if i else "empty"
)
def test_no_template_advice_states_a_bare_dpi_target(issues: list[Issue]) -> None:
    """No shipped customer-facing template may phrase a threshold as a target to hit.

    The threshold belongs in `Issue.message` and in the technical line, where an artist
    reads it - never in the customer's action.
    """
    for issue in issues:
        if issue.advice is None:
            continue
        assert not states_a_bare_dpi_target(issue.advice.action), (
            f"{issue.code}: the action names DPI as a target: {issue.advice.action}"
        )


# Forward commitments only. A consequence clause ("the cut would trim the edges") is the
# opposite of an offer and must not match, so "we would" is deliberately not on this list.
PROMISE_PHRASES = ("we will ", "we'll ", "we can ")


@pytest.mark.parametrize(
    "issues", CUSTOMER_FACING_ADVICE, ids=lambda i: str(i[0].code) if i else "empty"
)
def test_no_template_promises_work_on_our_side(issues: list[Issue]) -> None:
    """No customer-facing template may commit the shop to doing something.

    brief.md S11: "Not fixing the artwork. Detect and explain." and "Not pricing,
    scheduling, nesting, or anything downstream of approval." A drafted message that says
    "reply and we will rescale it for you" commits an artist to work the pipeline does not
    do, in text they may approve without reading closely.

    The first draft of this module shipped exactly that, plus an offer to move an order to
    a different product. The rescale offer was the worse of the two: it made a finding that
    may be a false reject (see docs/customer-message.md S4) read as good service, which is
    how a rule bug stays invisible.

    Present-tense statements of what the pipeline already does are fine and not matched
    here - "we convert it to CMYK for printing" is established behaviour. So is the
    letter's closing line, "we'll re-check it right away", because re-checking an upload is
    what the pipeline is. What is forbidden is a forward promise of new work.
    """
    for issue in issues:
        if issue.advice is None:
            continue
        for slot, text in (("action", issue.advice.action), ("avoid", issue.advice.avoid)):
            lowered = (text or "").lower()
            for phrase in PROMISE_PHRASES:
                assert phrase not in lowered, (
                    f"{issue.code}.{slot} promises work on our side "
                    f"({phrase.strip()!r}): {text}"
                )


def test_the_promise_rule_catches_the_wording_it_is_meant_to_catch() -> None:
    # Both of these shipped in the first draft of this module.
    for promised in (
        "reply and we will rescale it for you",
        "a different product and we'll move the order over",
    ):
        assert any(p in promised for p in PROMISE_PHRASES), promised
    assert not any(p in "export it again for a 5x3 in print" for p in PROMISE_PHRASES)


@pytest.mark.parametrize(
    "issues", CUSTOMER_FACING_ADVICE, ids=lambda i: str(i[0].code) if i else "empty"
)
def test_every_customer_facing_blocking_code_carries_advice(issues: list[Issue]) -> None:
    """A blocking finding is something we ask the customer to fix, so it must have a
    customer register. Without this test a new code ships with the artist's sentence as
    its customer letter, which is the state this module exists to end."""
    for issue in issues:
        if issue.severity is Severity.BLOCKING:
            assert issue.advice is not None, f"{issue.code} has no customer advice"


def test_resolution_advice_talks_in_pixels_not_a_bare_ratio() -> None:
    """DPI is pixels over inches: meaningless without the print size, and not reusable,
    because `min_dpi` is 72 on a banner and 300 on a roll label."""
    advice = advice_of(
        check_resolution(meta(declared_dpi=None, width_px=724, height_px=448), SPEC, ORDER),
        IssueCode.LOW_RESOLUTION,
    )
    assert "724x448 pixels" in advice.action
    assert "788x488" in advice.action  # 150 dpi over a 5.25x3.25 in canvas
    assert advice.avoid and "DPI box" in advice.avoid


def test_resolution_advice_does_not_ask_for_detail_the_file_already_has() -> None:
    """A file tagged 72 DPI with 3000 px has the detail and the wrong print size. Telling
    this customer to export larger sends them looking for pixels they already have."""
    advice = advice_of(
        check_resolution(meta(declared_dpi=72.0, width_px=3000, height_px=1857), SPEC, ORDER),
        IssueCode.LOW_RESOLUTION,
    )
    assert "plenty of detail" in advice.action
    assert "larger" not in advice.action
    assert "41.7x25.8 in" in advice.action  # 3000/72 x 1857/72


def test_softness_wording_scales_with_the_shortfall() -> None:
    """9% short and 60% short are the same code and different conversations."""
    near = advice_of(
        check_resolution(meta(declared_dpi=None, width_px=724, height_px=448), SPEC, ORDER),
        IssueCode.LOW_RESOLUTION,
    )
    far = advice_of(
        check_resolution(meta(declared_dpi=None, width_px=260, height_px=161), SPEC, ORDER),
        IssueCode.LOW_RESOLUTION,
    )
    assert "a little softer" in near.headline
    assert "blurry" in far.headline


def test_size_phrases_get_the_right_article() -> None:
    """"an 5x3 in sticker" reads as a typo, and a letter a customer is meant to trust
    cannot look machine-assembled."""
    five = advice_of(
        check_resolution(meta(declared_dpi=None, width_px=724, height_px=448), SPEC, ORDER),
        IssueCode.LOW_RESOLUTION,
    )
    assert "a 5x3 in" in five.action and "an 5x3" not in five.action

    order8 = OrderMetadata(
        order_id="A-8", product_id="die-cut-sticker", width_in=8, height_in=3, quantity=1
    )
    eight = advice_of(
        check_resolution(meta(declared_dpi=None, width_px=1100, height_px=413), SPEC, order8),
        IssueCode.LOW_RESOLUTION,
    )
    assert "an 8x3 in" in eight.action


def test_reviewer_only_findings_carry_no_customer_advice(tmp_path: Path) -> None:
    """The detector's blind spot is not the customer's problem to fix. A finding that
    exists to route the file to a person has no customer register, and `customer_letter`
    leaves it out rather than inventing an action."""
    flat = Image.new("CMYK", (788, 488), (0, 0, 0, 30))
    path = tmp_path / "flat.tif"
    flat.save(path, dpi=(150, 150))
    with Image.open(path) as img:
        img.load()
        issues, _boxes = analyse_pixels(img, SPEC, 150.0)

    inconclusive = [
        i
        for i in issues
        if i.code is IssueCode.TEXT_TOO_SMALL and i.severity is Severity.ADVISORY
    ]
    assert inconclusive, "expected the text detector to report nothing on a blank file"
    assert all(i.advice is None for i in inconclusive)


# --------------------------------------------------------------------------------------
# The agent arm
# --------------------------------------------------------------------------------------


def test_advice_survives_the_tool_payload_round_trip() -> None:
    """`_precompute` rebuilds issues from JSON before the model ever sees them, so advice
    that is not serialised is advice the agent arm loses in silence."""
    issues = check_resolution(
        meta(declared_dpi=None, width_px=724, height_px=448), SPEC, ORDER
    )
    payload = {"issues": _issues_payload(issues)}
    # Through a real JSON encode/decode, which is what dispatch() hands the model.
    rebuilt = issues_from_payload(json.loads(json.dumps(payload)))
    assert len(rebuilt) == 1
    assert rebuilt[0].advice is not None
    assert rebuilt[0].advice == issues[0].advice


def test_a_malformed_advice_block_never_costs_us_the_finding() -> None:
    """These issues were proven by a measurement. Dropping one because its customer
    wording failed to validate trades a real defect for a cosmetic field."""
    payload = {
        "issues": [
            {
                "code": "LOW_RESOLUTION",
                "severity": "BLOCKING",
                "message": "Artwork is 138 DPI.",
                "advice": {"headline": "", "action": ""},  # invalid: min_length=1
                "evidence": {"measured": 137.9, "required": 150.0, "unit": "dpi"},
            }
        ]
    }
    rebuilt = issues_from_payload(payload)
    assert len(rebuilt) == 1, "the finding was dropped along with its bad advice"
    assert rebuilt[0].code is IssueCode.LOW_RESOLUTION
    assert rebuilt[0].advice is None
