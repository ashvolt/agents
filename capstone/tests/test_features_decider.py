"""CV features and the decider — the no-vision-model pipeline.

Fixtures are drawn with PIL directly rather than produced by `capstone/data/generate.py`,
for the same reason as the bucket tests: the generator shares the features' assumptions
about what a defect looks like, so it cannot be the thing that grades them.

Offline. No API, no spend.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from capstone.evals.train_decider import FEATURES, export, fit
from capstone.src.deciders import (
    DEFAULT_MODEL,
    LogisticModel,
    decide,
    guard_reason,
)
from capstone.src.product_specs import get_spec
from capstone.src.schemas import (
    EscalationReason,
    IssueCode,
    OrderMetadata,
    PreflightCase,
    VerdictType,
)
from capstone.tools.features import FEATURE_NAMES, ArtworkFeatures, measure_margin_objects
from capstone.tools.text_detect import TextBox

STICKER = get_spec("die-cut-sticker")  # bleed 0.125in, safe zone 0.125in, 150 DPI minimum
DPI = 300.0
ORDER = OrderMetadata(
    order_id="ORD-T", product_id=STICKER.product_id, width_in=3.0, height_in=2.0, quantity=100
)
BG = (245, 243, 240)
INK = (20, 20, 20)

BLEED_PX = STICKER.bleed_in * DPI  # 37.5
SAFE_PX = STICKER.safe_zone_in * DPI  # 37.5
W = round((ORDER.width_in + 2 * STICKER.bleed_in) * DPI)  # 975
H = round((ORDER.height_in + 2 * STICKER.bleed_in) * DPI)  # 675


def canvas() -> Image.Image:
    return Image.new("RGB", (W, H), BG)


def margin(img: Image.Image, exclude: list[TextBox] | None = None) -> dict[str, float]:
    return measure_margin_objects(img, STICKER, ORDER, DPI, exclude=exclude)


def mark_at_depth(depth: float, edge: str) -> Image.Image:
    """A 40px element whose outer edge sits `depth` safe-zones into the keep-out margin."""
    img = canvas()
    d = ImageDraw.Draw(img)
    outer = BLEED_PX + SAFE_PX - depth * SAFE_PX  # distance from the canvas edge
    size = 40
    if edge == "left":
        d.rectangle((outer, 300, outer + size, 340), fill=INK)
    elif edge == "right":
        d.rectangle((W - 1 - outer - size, 300, W - 1 - outer, 340), fill=INK)
    elif edge == "top":
        d.rectangle((450, outer, 490, outer + size), fill=INK)
    else:
        d.rectangle((450, H - 1 - outer - size, 490, H - 1 - outer), fill=INK)
    return img


# --------------------------------------------------------------------------------------
# Margin objects
# --------------------------------------------------------------------------------------


def test_blank_artwork_has_nothing_in_the_margin() -> None:
    assert margin(canvas())["margin_objects"] == 0


def test_full_width_band_is_background_not_an_intrusion() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((0, 0, W, 50), fill=INK)
    assert margin(img)["margin_objects"] == 0


def test_band_is_judged_by_the_edge_it_spans_not_the_corner_it_touches() -> None:
    # Regression: a full-width band also touches the left and right edges at its corners,
    # where it is only 50px long. Judged by that side it looked like a short element deep
    # in the margin, and every clean file with a border scored as an intrusion.
    img = canvas()
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 50), fill=INK)
    d.rectangle((0, H - 50, W, H), fill=INK)
    result = margin(img)
    assert result["margin_objects"] == 0
    assert result["margin_depth_max"] == 0.0


def test_flat_band_has_no_protrusion() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((0, 0, W, 50), fill=INK)
    assert margin(img)["band_protrusions"] == 0


@pytest.mark.parametrize("edge", ["top", "bottom", "left"])
def test_element_merged_into_a_band_is_reported_as_a_protrusion(edge: str) -> None:
    # Found on the shifted holdout: a mark overlapping a full-width border merges into it
    # and was filed as background. Its depth is unmeasurable; its existence is not.
    img = canvas()
    d = ImageDraw.Draw(img)
    if edge == "top":
        d.rectangle((0, 0, W, 50), fill=INK)
        d.rectangle((450, 30, 490, 90), fill=INK)
    elif edge == "bottom":
        d.rectangle((0, H - 51, W, H), fill=INK)
        d.rectangle((450, H - 91, 490, H - 31), fill=INK)
    else:
        d.rectangle((0, 0, 50, H), fill=INK)
        d.rectangle((30, 300, 90, 340), fill=INK)
    result = margin(img)
    assert result["band_protrusions"] == 1
    assert result["margin_objects"] == 0  # it is not a free-standing object


def test_element_well_inside_the_safe_area_is_ignored() -> None:
    img = canvas()
    ImageDraw.Draw(img).rectangle((300, 250, 500, 400), fill=INK)
    assert margin(img)["margin_objects"] == 0


@pytest.mark.parametrize("depth", [0.5, 1.5])
@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_depth_is_measured_the_same_from_every_edge(depth: float, edge: str) -> None:
    # The generator only ever intrudes from the left. A feature that measured one edge
    # differently from another would let a classifier learn the generator's habit.
    result = margin(mark_at_depth(depth, edge))
    assert result["margin_objects"] == 1
    assert result["margin_depth_max"] == pytest.approx(depth, abs=0.05)


def test_depth_above_one_means_past_the_trim_line() -> None:
    inside = margin(mark_at_depth(0.9, "left"))["margin_depth_max"]
    past = margin(mark_at_depth(1.1, "left"))["margin_depth_max"]
    assert inside < 1.0 < past


def test_excluded_text_box_is_not_counted() -> None:
    img = mark_at_depth(1.5, "left")
    box = TextBox(x0=0, y0=295, x1=100, y1=345, glyph_count=4)
    assert margin(img, exclude=[box])["margin_objects"] == 0


def test_margin_unmeasurable_when_safe_zone_is_under_a_pixel() -> None:
    result = measure_margin_objects(canvas(), STICKER, ORDER, dpi=4.0)
    assert result["margin_objects"] == 0
    assert result["safe_zone_px"] == 0.0


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------


def features(**overrides: object) -> ArtworkFeatures:
    base: dict[str, object] = {
        "dpi_ratio": 2.0,
        "bleed_ratio": 1.0,
        "aspect_deviation_ratio": 0.0,
        "stroke_ratio": 2.0,
        "contrast_ratio": 2.0,
        "text_ratio": 1.8,
        "text_lines": 1,
        "stroke_half_pixel": 0.24,
        "text_half_pixel": 0.02,
        "edge_asymmetry": 0.0,
        "edge_coverage_max": 0.5,
        "edge_coverage_min": 0.0,
        "interior_coverage": 0.1,
        "margin_objects": 0,
        "margin_depth_max": 0.0,
        "margin_span_of_deepest": 0.0,
        "margin_area_ratio": 0.0,
        "safe_zone_px": 37.5,
        "band_protrusions": 0,
        "advisory_count": 0,
    }
    return ArtworkFeatures.model_validate({**base, **overrides})


def test_no_guard_on_an_ordinary_file() -> None:
    assert guard_reason(features()) is None


def test_no_text_found_is_always_escalated() -> None:
    assert "no text" in (guard_reason(features(text_lines=0, text_ratio=None)) or "")


def test_band_protrusion_is_always_escalated() -> None:
    assert "background band" in (guard_reason(features(band_protrusions=1)) or "")


def test_stroke_within_half_a_pixel_of_the_limit_is_undecidable() -> None:
    assert guard_reason(features(stroke_ratio=1.0)) is not None
    assert guard_reason(features(stroke_ratio=1.23)) is not None


def test_stroke_more_than_half_a_pixel_clear_is_decidable() -> None:
    assert guard_reason(features(stroke_ratio=1.25)) is None


def test_unmeasured_stroke_does_not_trip_the_guard() -> None:
    assert guard_reason(features(stroke_ratio=None)) is None


def test_feature_vector_keeps_none_distinct_from_zero() -> None:
    vec = features(stroke_ratio=None).vector()
    assert math.isnan(vec[FEATURE_NAMES.index("stroke_ratio")])
    assert vec[FEATURE_NAMES.index("margin_depth_max")] == 0.0


# --------------------------------------------------------------------------------------
# The logistic model
# --------------------------------------------------------------------------------------


def test_json_model_reproduces_sklearn_probabilities() -> None:
    # The stored model re-implements inference without scikit-learn. If the two ever
    # disagree, the model in production is not the model that was evaluated.
    rng = np.random.default_rng(0)
    x = rng.normal(size=(80, len(FEATURES)))
    x[:, FEATURES.index("margin_objects")] = rng.integers(0, 4, size=80)  # a count
    y = (x[:, 0] + 0.5 * rng.normal(size=80) > 0).astype(int)
    model, impute, norm = fit(x, y)
    lm = export(model, impute, norm, threshold=0.5, provenance={})

    expected = model.predict_proba((x - norm[0]) / norm[1])[:, 1]
    for row, p in zip(x, expected, strict=True):
        f = features(**dict(zip(FEATURES, row.tolist(), strict=True)))
        assert lm.p_defect(f) == pytest.approx(p, abs=1e-9)


def test_missing_values_are_imputed_not_crashed_on() -> None:
    x = np.array([[0.0, 0.1, 0.0, 0, 0.0], [2.0, 0.1, 0.01, 1, math.nan]] * 5)
    y = np.array([0, 1] * 5)
    model, impute, norm = fit(x, y)
    lm = export(model, impute, norm, threshold=0.5, provenance={})
    assert 0.0 <= lm.p_defect(features(edge_asymmetry=None)) <= 1.0


def test_committed_model_loads_and_is_monotone_in_depth() -> None:
    lm = LogisticModel.load(DEFAULT_MODEL)
    assert set(lm.features) <= set(FEATURE_NAMES)
    assert 0.0 < lm.threshold < 1.0
    shallow = lm.p_defect(
        features(
            margin_objects=1,
            margin_depth_max=0.5,
            margin_span_of_deepest=0.2,
            margin_area_ratio=0.01,
        )
    )
    deep = lm.p_defect(
        features(
            margin_objects=1,
            margin_depth_max=1.5,
            margin_span_of_deepest=0.2,
            margin_area_ratio=0.01,
        )
    )
    assert shallow < lm.threshold < deep


# --------------------------------------------------------------------------------------
# Triage policy, with a stub decider
# --------------------------------------------------------------------------------------


class StubDecider:
    name = "stub"

    def __init__(self, p: float) -> None:
        self.p = p
        self.calls = 0

    def p_defect(self, features: ArtworkFeatures) -> float:
        self.calls += 1
        return self.p


def glyph_row(img: Image.Image, glyph_h: int = 60) -> None:
    """Six glyph-sized marks, enough for the detector to call it a line of text."""
    d = ImageDraw.Draw(img)
    w = glyph_h // 2
    for i in range(6):
        x = 200 + i * (w + w // 2)
        d.rectangle((x, 300, x + w, 300 + glyph_h), fill=INK)


def saved_case(tmp_path: Path, img: Image.Image, dpi: float = DPI) -> PreflightCase:
    path = tmp_path / "art.tif"
    img.convert("CMYK").save(path, dpi=(dpi, dpi))
    return PreflightCase(case_id="t-1", image_path=path, order=ORDER)


def test_low_p_approves(tmp_path: Path) -> None:
    img = canvas()
    glyph_row(img)
    stub = StubDecider(0.01)
    verdict, p = decide(saved_case(tmp_path, img), stub, threshold=0.2)
    assert verdict.verdict is VerdictType.APPROVE
    assert p == 0.01 and stub.calls == 1


def test_high_p_escalates_with_evidence_and_never_requests_a_fix(tmp_path: Path) -> None:
    img = canvas()
    glyph_row(img)
    verdict, _ = decide(saved_case(tmp_path, img), StubDecider(0.9), threshold=0.2)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.LOW_CONFIDENCE
    assert verdict.issues[0].code is IssueCode.CONTENT_IN_SAFE_ZONE
    assert "p(defect)=0.90" in (verdict.issues[0].evidence.note or "")


def test_guard_escalates_without_asking_the_decider(tmp_path: Path) -> None:
    stub = StubDecider(0.0)
    verdict, p = decide(saved_case(tmp_path, canvas()), stub, threshold=0.2)  # no text
    assert verdict.verdict is VerdictType.ESCALATE
    assert p is None and stub.calls == 0
    # the escalation names what it is about, so the reviewer does not start from zero
    assert verdict.issues[0].code is IssueCode.TEXT_TOO_SMALL
    assert "guard:" in (verdict.issues[0].evidence.note or "")


def test_blocking_rule_finding_is_final_and_skips_the_decider(tmp_path: Path) -> None:
    # 100 DPI against a 150 DPI minimum: a measurement, not a probability.
    img = canvas().resize((round(W / 3), round(H / 3)))
    glyph_row(img, glyph_h=30)
    stub = StubDecider(0.0)
    verdict, p = decide(saved_case(tmp_path, img, dpi=DPI / 3), stub, threshold=0.2)
    assert verdict.verdict is VerdictType.REQUEST_FIX
    assert IssueCode.LOW_RESOLUTION in {i.code for i in verdict.issues}
    assert p is None and stub.calls == 0
