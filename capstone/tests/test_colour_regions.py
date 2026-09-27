"""Stroke measurement on colour regions rather than a brightness threshold.

Offline. No API, no spend.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from capstone.tools.bucket2_pixels import colour_regions, measure_min_stroke_px

DPI = 300.0
NAVY = (29, 53, 87)


def test_grey_rule_on_navy_is_measured() -> None:
    # Grey (62,63,63) on navy differs by ~24 dE, mostly in colour, barely in brightness.
    # The luminance mask never saw it; colour regions do.
    img = Image.new("RGB", (1200, 800), NAVY)
    d = ImageDraw.Draw(img)
    d.rectangle((200, 100, 700, 500), fill=(250, 250, 250))
    d.line((200, 650, 1000, 650), fill=(62, 63, 63), width=1)
    assert measure_min_stroke_px(img, dpi=DPI) == pytest.approx(1.0)


def test_antialiased_edge_between_two_inks_is_not_a_stroke() -> None:
    # Two solid blocks meeting along an antialiased diagonal. The in-between pixels are
    # reassigned to a real colour, so no one-pixel band appears between them.
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.polygon([(100, 100), (700, 100), (100, 700)], fill=(230, 57, 70))
    d.polygon([(700, 100), (700, 700), (100, 700)], fill=(42, 157, 143))
    big = img.resize((1600, 1600), Image.LANCZOS).resize((800, 800), Image.LANCZOS)
    assert measure_min_stroke_px(big, dpi=DPI) > 20


def test_gradient_is_one_region_not_a_stack_of_bands() -> None:
    ramp = np.tile(np.linspace(80, 200, 600).astype(np.uint8), (400, 1))
    arr = np.stack([ramp, ramp, ramp], axis=2)
    canvas = np.full((600, 800, 3), 255, dtype=np.uint8)
    canvas[100:500, 100:700] = arr
    img = Image.fromarray(canvas)
    regions, _background = colour_regions(img)
    assert len(np.unique(regions[100:500, 100:700])) <= 3  # the 16-level steps merge
    # and so the gradient's quantisation steps are never reported as hairlines
    assert measure_min_stroke_px(img, dpi=DPI) > 20
