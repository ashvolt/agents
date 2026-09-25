"""Scene documents, verified fixes, and claim-checked narration.

Fixtures are drawn with PIL directly, not by the generator, for the same reason as the
other capstone tests: the generator shares the pipeline's assumptions about what a defect
looks like.

Offline except the one test marked `integration`, which is skipped without a key.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from capstone.src.narrate import (
    Narration,
    check_claims,
    narrate_with_model,
    template_narration,
    user_message,
)
from capstone.src.product_specs import get_spec
from capstone.src.schemas import IssueCode, OrderMetadata, Verdict, VerdictType
from capstone.tools.fixes import MIN_AUTO_SCALE, FixPlan, plan_fixes
from capstone.tools.scene import describe, extract_scene
from capstone.tools.text_detect import TextBox
from shared.env import cheap_model, has_api_key

STICKER = get_spec("die-cut-sticker")  # bleed 0.125in, safe 0.125in, 150 DPI min, CMYK
DPI = 300.0
ORDER = OrderMetadata(
    order_id="ORD-T", product_id=STICKER.product_id, width_in=3.0, height_in=2.0, quantity=100
)
BG = (245, 243, 240)
INK = (20, 20, 20)
W = round((ORDER.width_in + 2 * STICKER.bleed_in) * DPI)  # 975
H = round((ORDER.height_in + 2 * STICKER.bleed_in) * DPI)  # 675
INSET = round((STICKER.bleed_in + STICKER.safe_zone_in) * DPI)  # 75 px to the safe line

ESCALATE = Verdict(
    verdict=VerdictType.ESCALATE,
    confidence=0.5,
    escalation_reason="LOW_CONFIDENCE",
    checks_completed=[],
)


def canvas(width: int = W, height: int = H) -> Image.Image:
    return Image.new("RGB", (width, height), BG)


def scene_of(img: Image.Image, dpi: float = DPI, boxes: list[TextBox] | None = None):
    return describe(img, STICKER, ORDER, dpi, boxes or [], [])


# --------------------------------------------------------------------------------------
# Scene
# --------------------------------------------------------------------------------------


def test_element_position_is_in_inches_from_the_trim() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((337, 187, 636, 486), fill=INK)  # 300 px square
    (e,) = scene_of(img).elements
    assert e.shape == "rect"
    assert e.x_in == pytest.approx((337 - STICKER.bleed_in * DPI) / DPI, abs=0.005)
    assert e.w_in == pytest.approx(1.0, abs=0.005)
    assert e.clearance_in > 0  # inside the safe area


def test_element_in_the_margin_has_negative_clearance_toward_that_edge() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((INSET - 30, 300, INSET + 30, 340), fill=INK)
    (e,) = scene_of(img).elements
    assert e.nearest_edge == "left"
    assert e.clearance_in == pytest.approx(-30 / DPI, abs=0.005)


def test_full_width_band_is_marked_as_spanning() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((0, 0, W, 40), fill=INK)
    (e,) = scene_of(img).elements
    assert e.spans_edge


def test_model_view_drops_pixel_boxes() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((300, 300, 400, 400), fill=INK)
    assert all("px" not in e for e in scene_of(img).for_model()["elements"])


def test_fidelity_is_high_for_flat_artwork() -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 40), fill=(200, 40, 40))
    d.rectangle((300, 250, 500, 400), fill=INK)
    d.ellipse((550, 200, 800, 450), outline=INK, width=12)
    assert scene_of(img).fidelity >= 0.95


def test_fidelity_is_low_when_the_description_cannot_capture_the_art() -> None:
    # A photograph-like gradient does not decompose into flat elements. Low fidelity here
    # is the signal working: a text model should not be trusted to reason about it.
    ramp = np.tile(np.linspace(0, 255, W, dtype=np.uint8), (H, 1))
    noise = np.random.default_rng(0).integers(0, 40, size=(H, W), dtype=np.uint8)
    arr = np.stack([ramp, (ramp // 2 + noise), 255 - ramp], axis=2)
    assert scene_of(Image.fromarray(arr)).fidelity < 0.9


def test_text_element_carries_size_but_never_content() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((300, 300, 600, 340), fill=INK)
    box = TextBox(x0=300, y0=300, x1=601, y1=341, glyph_count=8)
    scene = extract_scene(img, STICKER, ORDER, DPI, [box], [])
    (text,) = [e for e in scene.for_model()["elements"] if e["shape"] == "text"]
    assert text["height_pt"] > 0
    # the structural injection defence: nothing in the model input can carry glyphs
    assert set(text) <= set(scene.elements[0].model_fields)


# --------------------------------------------------------------------------------------
# Fixes
# --------------------------------------------------------------------------------------


def test_missing_bleed_is_mirrored_out_and_verified(tmp_path: Path) -> None:
    short = canvas(W - 30, H - 30)  # 15 px short of bleed on every edge
    ImageDraw.Draw(short).rectangle((300, 250, 500, 400), fill=INK)
    scene = scene_of(short)
    plan = plan_fixes(short, scene, ORDER, {IssueCode.MISSING_BLEED.value}, tmp_path)
    (fix,) = [f for f in plan.fixes if f.code is IssueCode.MISSING_BLEED]
    assert fix.kind == "applied" and fix.verified
    with Image.open(plan.rectified) as out:
        assert out.size == (W, H)
        assert out.mode == "CMYK"


def test_element_in_the_margin_is_fitted_inside_and_verified(tmp_path: Path) -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.rectangle((INSET - 20, 300, INSET + 60, 360), fill=INK)  # 20 px into the margin
    d.rectangle((400, 250, 800, 450), fill=(40, 40, 160))
    plan = plan_fixes(img, scene_of(img), ORDER, set(), tmp_path)
    (fix,) = [f for f in plan.fixes if f.code is IssueCode.CONTENT_IN_SAFE_ZONE]
    assert fix.kind == "applied" and fix.verified
    assert MIN_AUTO_SCALE <= float(fix.params["scale"]) <= 1.0
    with Image.open(plan.rectified) as out:
        after = extract_scene(out.convert("RGB"), STICKER, ORDER, DPI, [], [])
    assert all(e.clearance_in >= 0 for e in after.elements if not e.spans_edge)


def test_large_shrink_is_suggested_not_applied(tmp_path: Path) -> None:
    img = canvas()
    # 635 px tall against a 521 px safe area: fitting it needs an 82% shrink
    ImageDraw.Draw(img).rectangle((300, 20, 600, H - 20), fill=INK)
    plan = plan_fixes(img, scene_of(img), ORDER, set(), tmp_path)
    (fix,) = [f for f in plan.fixes if f.code is IssueCode.CONTENT_IN_SAFE_ZONE]
    assert fix.kind == "suggested"
    assert float(fix.params["scale"]) < MIN_AUTO_SCALE
    assert plan.rectified is None


def test_bleed_is_not_padded_when_proportions_are_also_wrong(tmp_path: Path) -> None:
    # Regression, holdout_v3 case-00521: padding a stretched file to the right canvas made
    # every rule pass while the design inside stayed stretched. Bleed missing on its own is
    # missing equally on every side.
    stretched = canvas(W - 30, H - 90)
    ImageDraw.Draw(stretched).rectangle((300, 200, 500, 350), fill=INK)
    plan = plan_fixes(
        stretched, scene_of(stretched), ORDER, {IssueCode.MISSING_BLEED.value}, tmp_path
    )
    (fix,) = [f for f in plan.fixes if f.code is IssueCode.MISSING_BLEED]
    assert fix.kind == "suggested"
    assert plan.rectified is None


def test_one_unverified_fix_blocks_the_whole_proof() -> None:
    from capstone.tools.fixes import Fix

    good = Fix(id="F1", code=IssueCode.THIN_LINES, kind="applied", action="a", verified=True)
    bad = Fix(
        id="F2", code=IssueCode.CONTENT_IN_SAFE_ZONE, kind="applied", action="b", verified=False
    )
    ready = FixPlan(fixes=(good,), before=("THIN_LINES",), after=(), rectified=Path("x.tif"))
    blocked = ready.model_copy(update={"fixes": (good, bad)})
    leftover = ready.model_copy(update={"after": ("TEXT_TOO_SMALL",)})
    assert ready.proof_ready
    assert not blocked.proof_ready
    assert not leftover.proof_ready  # still needs the customer


def test_low_resolution_is_never_upscaled_only_priced(tmp_path: Path) -> None:
    img = canvas(W // 3, H // 3)  # 100 DPI at the ordered size
    ImageDraw.Draw(img).rectangle((100, 80, 200, 140), fill=INK)
    plan = plan_fixes(
        img, scene_of(img, dpi=DPI / 3), ORDER, {IssueCode.LOW_RESOLUTION.value}, tmp_path
    )
    (fix,) = plan.fixes
    assert fix.kind == "suggested"
    assert fix.params["max_w_in"] == pytest.approx((W // 3) / STICKER.min_dpi, abs=0.01)
    assert plan.rectified is None


# --------------------------------------------------------------------------------------
# Narration and the claim check
# --------------------------------------------------------------------------------------


def planned(tmp_path: Path) -> tuple[object, FixPlan]:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.rectangle((INSET - 20, 300, INSET + 60, 360), fill=INK)
    d.rectangle((400, 250, 800, 450), fill=(40, 40, 160))
    scene = scene_of(img)
    return scene, plan_fixes(img, scene, ORDER, set(), tmp_path)


def test_template_narration_passes_its_own_check(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    narration = template_narration(scene, plan, ESCALATE)
    assert check_claims(narration, scene, plan) == []
    assert "E1" not in narration.customer_message  # ids are for reviewers only


def test_invented_number_is_caught(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    bad = Narration(customer_message="We moved it 0.42 in inward.", reviewer_note="")
    assert "unsupported number 0.42" in check_claims(bad, scene, plan)


def test_invented_id_is_caught(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    bad = Narration(customer_message="", reviewer_note="See E99 and F7.")
    problems = check_claims(bad, scene, plan)
    assert "unknown id E99" in problems and "unknown id F7" in problems


def test_rounded_and_percentage_forms_of_real_numbers_pass(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    scale = float(next(f for f in plan.fixes).params["scale"])
    ok = Narration(
        customer_message=f"We scaled the design to {scale * 100:.0f}% to clear a 0.13 in margin.",
        reviewer_note="",
    )
    assert check_claims(ok, scene, plan) == []


class FakeClient:
    """Stands in for anthropic.Anthropic: returns one scripted tool call."""

    def __init__(self, tool_input: dict[str, object] | None) -> None:
        self.tool_input = tool_input
        self.messages = self
        self.sent: dict[str, object] = {}

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.sent = kwargs
        content = []
        if self.tool_input is not None:
            content.append(
                SimpleNamespace(type="tool_use", name="write_explanation", input=self.tool_input)
            )
        return SimpleNamespace(content=content, stop_reason="end_turn")


def test_model_narration_with_a_false_number_falls_back_to_the_template(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    liar = FakeClient(
        {
            "customer_message": "Your logo was 3.7 in from the edge; we fixed it.",
            "reviewer_note": "E1 moved.",
            "cited_elements": ["E1"],
            "cited_fixes": ["F1"],
        }
    )
    shipped, problems = narrate_with_model(liar, "stub", scene, plan, ESCALATE)
    assert shipped.source == "template"
    assert "unsupported number 3.7" in problems


def test_model_narration_that_checks_out_is_used(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    honest = FakeClient(
        {
            "customer_message": "We nudged your design inward so nothing sits near the cut. "
            "Please approve the proof.",
            "reviewer_note": "F1 verified; E1 was inside the margin.",
            "cited_elements": ["E1"],
            "cited_fixes": ["F1"],
        }
    )
    shipped, problems = narrate_with_model(honest, "stub", scene, plan, ESCALATE)
    assert problems == [] and shipped.source == "stub"
    # the model received measurements, not an image
    content = honest.sent["messages"][0]["content"]  # type: ignore[index]
    assert isinstance(content, str) and '"elements"' in content


def test_no_tool_call_falls_back(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    shipped, problems = narrate_with_model(FakeClient(None), "stub", scene, plan, ESCALATE)
    assert shipped.source == "template" and problems


def test_low_fidelity_scene_is_never_sent_to_a_model(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    blurry = scene.model_copy(update={"fidelity": 0.6})
    client = FakeClient(
        {"customer_message": "", "reviewer_note": "", "cited_elements": [], "cited_fixes": []}
    )
    shipped, problems = narrate_with_model(client, "stub", blurry, plan, ESCALATE)
    assert shipped.source == "template"
    assert client.sent == {}  # no request was made
    assert "fidelity" in problems[0]


def test_model_input_is_deterministic(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    assert user_message(scene, plan, ESCALATE) == user_message(scene, plan, ESCALATE)


@pytest.mark.integration
@pytest.mark.skipif(not has_api_key(), reason="no ANTHROPIC_API_KEY in .env")
def test_real_model_narration_passes_the_claim_check(tmp_path: Path) -> None:
    import anthropic

    scene, plan = planned(tmp_path)
    shipped, problems = narrate_with_model(
        anthropic.Anthropic(), cheap_model(), scene, plan, ESCALATE
    )
    assert problems == [], problems
    assert shipped.source == cheap_model()
