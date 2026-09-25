"""DBNet text detection and text-line contrast, on real type.

These need the optional `rapidocr_onnxruntime` dependency (the model ships in its wheel)
and are skipped without it. Offline; no network, no API.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from capstone.src.product_specs import get_spec
from capstone.tools.bucket2_pixels import (
    check_contrast,
    measure_min_stroke_px,
    measure_text_contrast,
)

pytest.importorskip("rapidocr_onnxruntime")

from capstone.tools.text_detect import DBNetDetector  # noqa: E402

STICKER = get_spec("die-cut-sticker")  # min contrast dE 15
DPI = 300.0
DETECTOR = DBNetDetector()


def caption(text: str, px: int, fill=(30, 30, 30), bg=(255, 255, 255), rule_px: int = 0):  # noqa: ANN001, ANN201
    img = Image.new("RGB", (1200, 500), bg)
    d = ImageDraw.Draw(img)
    d.text((100, 200), text, font=ImageFont.load_default(size=px), fill=fill)
    if rule_px:
        d.line((100, 200 + int(px * 1.4), 900, 200 + int(px * 1.4)), fill=fill, width=rule_px)
    return img


def test_finds_antialiased_type_on_a_coloured_background() -> None:
    img = caption("FRESH ROASTED", 60, fill=(240, 240, 240), bg=(42, 157, 143))
    assert len(DETECTOR.detect(img)) == 1


def test_box_is_tight_to_the_ink_not_the_padded_detection() -> None:
    # Capital height of the bundled face is ~0.71 em; a padded DBNet box would be ~1.1+.
    (box,) = DETECTOR.detect(caption("LIMITED EDITION", 80))
    assert 0.6 * 80 <= box.height_px <= 0.8 * 80


def test_rule_under_the_caption_is_not_counted_as_text_height() -> None:
    (box,) = DETECTOR.detect(caption("HOMEMADE", 60, rule_px=3))
    assert box.height_px <= 0.8 * 60


def test_detection_is_deterministic() -> None:
    img = caption("SMALL BATCH", 50)
    assert DETECTOR.detect(img) == DETECTOR.detect(img)


def test_low_contrast_caption_is_measured_as_a_line() -> None:
    # A pale caption: every letter is a region far smaller than the element floor, so
    # element-based contrast never sees it. Measured as a line, it is caught.
    img = caption("ORGANIC", 60, fill=(222, 222, 222))  # dE ~10.7 against a minimum of 15
    boxes = DETECTOR.detect(img)
    de, _bg, _ink = measure_text_contrast(img, boxes)
    assert 9.0 <= de < STICKER.min_contrast_delta_e
    assert [i.code.value for i in check_contrast(img, STICKER, boxes)] == ["LOW_CONTRAST"]


def test_legible_caption_passes_contrast() -> None:
    img = caption("ORGANIC", 60)
    assert check_contrast(img, STICKER, DETECTOR.detect(img)) == []


def test_text_fringe_is_not_measured_as_a_stroke() -> None:
    # Before the exclusion pad, antialiased pixels just outside a tight text box were read
    # as one-pixel strokes. Only type on the canvas: there should be no stroke at all.
    img = caption("GOOD VIBES", 60)
    assert measure_min_stroke_px(img, exclude=DETECTOR.detect(img), dpi=DPI) is None


def test_long_hairline_is_measured_even_next_to_dense_artwork() -> None:
    img = Image.new("RGB", (1200, 900), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 1100, 600), fill=(20, 20, 20))  # a lot of ink
    d.line((100, 750, 800, 750), fill=(20, 20, 20), width=1)  # 700 px, well under 0.2%
    assert measure_min_stroke_px(img, dpi=DPI) == pytest.approx(1.0)
    # without a resolution the old share-of-ink floor applies and the line is skipped
    assert measure_min_stroke_px(img) != pytest.approx(1.0)


def test_square_detection_is_not_a_text_line() -> None:
    # DBNet fires on round illustration parts; called "text", they were excluded from the
    # cut-line measurement and hid two real intrusions. A line of text is wide.
    img = Image.new("RGB", (1200, 900), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((100, 100, 400, 400), fill=(250, 200, 40), outline=(20, 20, 20), width=8)
    d.ellipse((190, 190, 230, 230), fill=(20, 20, 20))
    d.ellipse((270, 190, 310, 230), fill=(20, 20, 20))
    d.text((100, 600), "SMALL BATCH", font=ImageFont.load_default(size=60), fill=(30, 30, 30))
    boxes = DETECTOR.detect(img)
    assert boxes and all(b.width_px >= 1.2 * b.height_px for b in boxes)
