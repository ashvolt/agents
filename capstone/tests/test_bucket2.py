"""Bucket 2 — pixel analysis.

As with bucket 1, fixtures are drawn with PIL directly rather than produced by
`capstone/data/generate.py`, so the checks are not being graded by the code that shares
their assumptions.

These four checks are the ones research.md D-1 moved *out* of the judgement bucket. If
they do not hold up under direct measurement, that reassignment was wrong and the model
should have kept them.

Offline. No API, no spend.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from capstone.src.product_specs import get_spec
from capstone.src.schemas import IssueCode, ProductSpec
from capstone.tools.bucket2_pixels import (
    check_contrast,
    check_stroke_width,
    check_text_size,
    check_transparency,
    delta_e76,
    measure_contrast,
    measure_min_stroke_px,
)
from capstone.tools.text_detect import ConnectedComponentDetector, TextBox, detect_text

STICKER = get_spec("die-cut-sticker")  # min_stroke 0.5pt, min_contrast dE 15, min_text 6pt
BANNER = get_spec("vinyl-banner")  # allows_transparency = False
DPI = 300.0
BG = (245, 243, 240)

PT_PER_INCH = 72.0


def pt_to_px(pt: float, dpi: float = DPI) -> int:
    return max(1, round(pt / PT_PER_INCH * dpi))


def canvas(w: int = 900, h: int = 600, mode: str = "RGB") -> Image.Image:
    return Image.new(mode, (w, h), BG if mode == "RGB" else BG + (255,))


def codes(issues) -> set[IssueCode]:  # noqa: ANN001
    return {i.code for i in issues}


# --------------------------------------------------------------------------------------
# Stroke width
# --------------------------------------------------------------------------------------


def art_with_strokes(stroke_px: int, *, add_solid_panel: bool = True) -> Image.Image:
    """Lines of a known width, optionally beside a large solid block.

    The panel matters: the original implementation took a 5th-percentile stroke width
    across all ink, so a hairline occupying 1% of the pixels vanished behind a solid
    shape. Including one here keeps that regression visible.
    """
    img = canvas()
    d = ImageDraw.Draw(img)
    if add_solid_panel:
        d.rectangle((40, 40, 400, 300), fill=(20, 20, 20))
    for i in range(3):
        y = 400 + i * (stroke_px * 4 + 6)
        d.line((60, y, 840, y), fill=(20, 20, 20), width=stroke_px)
    return img


@pytest.mark.parametrize(
    ("stroke_pt", "should_flag"),
    [
        (2.0, False),  # 4x the limit
        (0.75, False),  # 1.5x -> 3px
        (0.65, False),  # 3px, the first width that clears the limit once rasterised
        (0.55, True),  # inside the quantisation band - see the test below
        (0.30, True),  # 0.6x
        (0.15, True),  # 0.3x
    ],
)
def test_stroke_width_boundary(stroke_pt: float, should_flag: bool) -> None:
    img = art_with_strokes(pt_to_px(stroke_pt))
    issues = check_stroke_width(img, STICKER, DPI)
    assert bool(issues) is should_flag, f"{stroke_pt}pt against {STICKER.min_stroke_pt}pt"


def test_rasterisation_creates_a_false_reject_band_just_above_the_limit() -> None:
    """A stroke can be nominally legal and still measure short once drawn.

    At 300 DPI one pixel is 0.24 pt and the 0.5 pt minimum is 2.08 px. Every nominal
    width from 0.40 to 0.60 pt rounds to 2 px, which measures back as 0.48 pt - below the
    limit. So a legitimate 0.55 pt stroke is reported as too thin.

    This is not a bug to fix by loosening the threshold. The file genuinely contains a
    2 px stroke, and at this resolution the check cannot resolve finer than 0.24 pt.
    Given the choice, Principle I says take the recoverable error: a false reject costs a
    customer one round trip, a false approve costs a misprint.

    The band widens as DPI falls. At 150 DPI one pixel is 0.48 pt, so the 0.5 pt minimum
    is 1.04 px and nearly every thin stroke lands in the band. Documented in limits.md S4.
    """
    one_px_pt = 1 / DPI * 72.0
    assert one_px_pt == pytest.approx(0.24, abs=0.01)

    legal_but_flagged = check_stroke_width(art_with_strokes(pt_to_px(0.55)), STICKER, DPI)
    assert codes(legal_but_flagged) == {IssueCode.THIN_LINES}
    assert legal_but_flagged[0].evidence.measured == pytest.approx(0.48, abs=0.01)

    # One pixel wider clears it.
    assert check_stroke_width(art_with_strokes(3), STICKER, DPI) == []


def test_a_hairline_is_not_hidden_by_a_solid_panel() -> None:
    # The regression that forced per-component measurement.
    thin = check_stroke_width(art_with_strokes(pt_to_px(0.15), add_solid_panel=True), STICKER, DPI)
    assert codes(thin) == {IssueCode.THIN_LINES}


def test_measured_stroke_is_close_to_what_was_drawn() -> None:
    for px in (2, 4, 8):
        measured = measure_min_stroke_px(art_with_strokes(px, add_solid_panel=False))
        assert measured == pytest.approx(px, abs=1), f"drew {px}px, measured {measured}"


def test_blank_image_has_no_stroke_finding() -> None:
    assert check_stroke_width(canvas(), STICKER, DPI) == []


def test_text_regions_are_excluded_from_stroke_measurement() -> None:
    # Letterforms are legitimately thinner than the artwork minimum at small sizes.
    # Counting them here would flag every file carrying small type.
    img = art_with_strokes(pt_to_px(2.0), add_solid_panel=False)
    d = ImageDraw.Draw(img)
    for i in range(5):  # a row of hairline "glyphs"
        x = 100 + i * 30
        d.rectangle((x, 100, x + 1, 130), fill=(10, 10, 10))
    box = TextBox(x0=90, y0=90, x1=260, y1=140, glyph_count=5)
    assert check_stroke_width(img, STICKER, DPI, exclude=[box]) == []


# --------------------------------------------------------------------------------------
# Contrast
# --------------------------------------------------------------------------------------


def art_with_element(colour: tuple[int, int, int], *, with_bold_accent: bool) -> Image.Image:
    img = canvas()
    d = ImageDraw.Draw(img)
    if with_bold_accent:
        d.rectangle((0, 0, 900, 60), fill=(10, 10, 10))
    d.rectangle((200, 200, 700, 450), fill=colour)
    return img


def colour_at_delta_e(target: float) -> tuple[int, int, int]:
    """Pick a grey whose deltaE from the background is closest to `target`."""
    best, best_err = (0, 0, 0), float("inf")
    for v in range(256):
        err = abs(delta_e76(BG, (v, v, v)) - target)
        if err < best_err:
            best, best_err = (v, v, v), err
    return best


@pytest.mark.parametrize(
    ("target_de", "should_flag"),
    [(60.0, False), (25.0, False), (16.0, False), (13.0, True), (7.0, True)],
)
def test_contrast_boundary(target_de: float, should_flag: bool) -> None:
    img = art_with_element(colour_at_delta_e(target_de), with_bold_accent=False)
    issues = check_contrast(img, STICKER)
    assert bool(issues) is should_flag, f"dE~{target_de} against {STICKER.min_contrast_delta_e}"


def test_a_faint_element_is_found_beside_a_bold_one() -> None:
    # The original bug: element discovery went through ink_mask, which thresholds relative
    # to the largest deviation present. A bold accent made the faint element read as
    # background, and the file reported perfect contrast — a false approve on exactly the
    # case this check exists for.
    img = art_with_element(colour_at_delta_e(7.0), with_bold_accent=True)
    assert codes(check_contrast(img, STICKER)) == {IssueCode.LOW_CONTRAST}


def test_antialiasing_halos_do_not_count_as_faint_elements() -> None:
    # Smooth edges create intermediate colours between ink and background. Scoring colour
    # bins independently reported those as faint elements on nearly every clean file.
    img = canvas()
    d = ImageDraw.Draw(img)
    d.ellipse((200, 150, 700, 450), fill=(15, 15, 15))
    img = img.resize((450, 300), Image.LANCZOS).resize((900, 600), Image.LANCZOS)
    assert check_contrast(img, STICKER) == []


def test_measure_contrast_returns_the_weakest_element() -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 900, 200), fill=(10, 10, 10))  # bold
    d.rectangle((100, 300, 800, 550), fill=colour_at_delta_e(9))  # faint
    measured = measure_contrast(img)
    assert measured is not None
    assert measured[0] < 15, "should report the weak element, not the bold one"


def test_flat_image_yields_no_contrast_finding() -> None:
    assert check_contrast(canvas(), STICKER) == []


# --------------------------------------------------------------------------------------
# Transparency
# --------------------------------------------------------------------------------------


def art_with_alpha_hole(radius: int) -> Image.Image:
    img = canvas(mode="RGBA")
    px = img.load()
    cx, cy = 450, 300
    for y in range(max(0, cy - radius), min(600, cy + radius)):
        for x in range(max(0, cx - radius), min(900, cx + radius)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                r, g, b, _ = px[x, y]
                px[x, y] = (r, g, b, 0)
    return img


def test_transparency_flagged_where_the_product_disallows_it() -> None:
    assert codes(check_transparency(art_with_alpha_hole(120), BANNER)) == {
        IssueCode.UNINTENDED_TRANSPARENCY
    }


def test_transparency_ignored_where_the_product_allows_it() -> None:
    # A die-cut sticker's shape is defined by the cut line; alpha is expected.
    assert check_transparency(art_with_alpha_hole(120), STICKER) == []


def test_opaque_rgba_is_not_flagged() -> None:
    assert check_transparency(canvas(mode="RGBA"), BANNER) == []


def test_a_few_stray_transparent_pixels_are_ignored() -> None:
    # An export artefact, not a hole the customer will notice.
    assert check_transparency(art_with_alpha_hole(3), BANNER) == []


def test_transparency_evidence_locates_the_hole() -> None:
    issue = check_transparency(art_with_alpha_hole(120), BANNER)[0]
    assert issue.evidence.region is not None
    x0, y0, x1, y1 = issue.evidence.region
    assert x0 < 450 < x1 and y0 < 300 < y1


def test_cmyk_file_has_no_alpha_to_check() -> None:
    assert check_transparency(canvas().convert("CMYK"), BANNER) == []


# --------------------------------------------------------------------------------------
# Text size
# --------------------------------------------------------------------------------------


# The glyph-row fixtures are rectangles, not type: they exercise the component detector's
# grouping logic, so they name it. DBNet (the default since real-art.md) is tested with
# real type in test_text_dbnet.py.
COMPONENTS = ConnectedComponentDetector()


def art_with_glyph_row(glyph_h_px: int, n: int = 6) -> Image.Image:
    """A row of glyph-sized marks. Exercises detection and grouping, not a font."""
    img = canvas()
    d = ImageDraw.Draw(img)
    w = max(1, glyph_h_px // 2)
    for i in range(n):
        x = 100 + i * (w + max(2, w // 2))
        d.rectangle((x, 300, x + w, 300 + glyph_h_px), fill=(15, 15, 15))
    return img


def test_detector_groups_a_row_into_one_line() -> None:
    boxes = detect_text(art_with_glyph_row(40), COMPONENTS)
    assert len(boxes) == 1
    assert boxes[0].glyph_count >= 3


def test_detector_ignores_a_pair_of_marks() -> None:
    # Fewer than three glyphs is more likely artwork than writing. Documented limitation:
    # this also discards genuinely short words.
    assert detect_text(art_with_glyph_row(40, n=2), COMPONENTS) == []


@pytest.mark.parametrize(
    ("text_pt", "should_flag"),
    [(18.0, False), (9.0, False), (6.5, False), (5.0, True), (3.0, True)],
)
def test_text_size_boundary(text_pt: float, should_flag: bool) -> None:
    boxes = detect_text(art_with_glyph_row(pt_to_px(text_pt)), COMPONENTS)
    assert boxes, "fixture produced no detectable text"
    issues = check_text_size(boxes, STICKER, DPI)
    assert bool(issues) is should_flag, f"{text_pt}pt against {STICKER.min_text_pt}pt"


def test_no_detected_text_yields_no_finding() -> None:
    # Absence of detected text is NOT evidence of absence — see the detector's documented
    # weaknesses. The check stays silent rather than asserting the file is clean.
    assert check_text_size([], STICKER, DPI) == []


def test_text_size_evidence_locates_the_smallest_line() -> None:
    boxes = detect_text(art_with_glyph_row(pt_to_px(3.0)), COMPONENTS)
    issue = check_text_size(boxes, STICKER, DPI)[0]
    assert issue.evidence.region is not None
    assert issue.evidence.required == STICKER.min_text_pt


def test_height_pt_conversion_round_trips() -> None:
    box = TextBox(x0=0, y0=0, x1=50, y1=pt_to_px(12.0), glyph_count=4)
    assert box.height_pt(DPI) == pytest.approx(12.0, abs=0.3)


# --------------------------------------------------------------------------------------
# Determinism — SC-008
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec", [STICKER, BANNER], ids=lambda s: s.product_id if isinstance(s, ProductSpec) else str(s)
)
def test_checks_are_reproducible(spec: ProductSpec) -> None:
    img = art_with_strokes(pt_to_px(0.3))
    first = [check_stroke_width(img, spec, DPI), check_contrast(img, spec)]
    second = [check_stroke_width(img, spec, DPI), check_contrast(img, spec)]
    assert [codes(x) for x in first] == [codes(x) for x in second]


def test_measurements_are_bit_identical_across_calls() -> None:
    img = art_with_strokes(4, add_solid_panel=False)
    assert measure_min_stroke_px(img) == measure_min_stroke_px(img)
    assert np.isclose(measure_contrast(img)[0], measure_contrast(img)[0])
