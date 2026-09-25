"""The AI-art builder: background cut-out, and raster art placed by its ink.

Needs the optional `data` dependencies (the real-art module imports cairosvg).
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cairosvg")

from PIL import Image, ImageDraw  # noqa: E402

from capstone.data.ai_art import cut_out  # noqa: E402
from capstone.data.real_art import _fit_raster, _ink_box  # noqa: E402


def _logo_on(background: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (200, 160), background)
    ImageDraw.Draw(img).ellipse((50, 30, 150, 130), fill=(20, 20, 20))
    return img


def test_flat_border_is_cut_out_to_the_ink() -> None:
    art, cutout = cut_out(_logo_on((250, 250, 250)))
    assert cutout
    assert _ink_box(art) == (50, 30, 151, 131)


def test_noisy_flat_border_is_still_flat() -> None:
    # generated backgrounds are never exactly one colour
    rng = np.random.default_rng(0)
    img = np.asarray(_logo_on((240, 236, 228))).astype(int)
    img = np.clip(img + rng.integers(-8, 9, img.shape), 0, 255).astype(np.uint8)
    art, cutout = cut_out(Image.fromarray(img))
    assert cutout
    x0, y0, x1, y1 = _ink_box(art)
    assert abs(x0 - 50) <= 2 and abs(y0 - 30) <= 2 and abs(x1 - 151) <= 2 and abs(y1 - 131) <= 2


def test_background_colour_inside_the_art_is_kept() -> None:
    # a white highlight enclosed by ink is part of the art, not the background
    img = _logo_on((255, 255, 255))
    ImageDraw.Draw(img).ellipse((90, 70, 110, 90), fill=(255, 255, 255))
    art, _ = cut_out(img)
    assert np.asarray(art)[80, 100, 3] == 255
    assert np.asarray(art)[5, 5, 3] == 0


def test_busy_border_keeps_the_whole_rectangle() -> None:
    gradient = np.linspace(0, 255, 200, dtype=np.uint8)
    img = np.dstack([np.tile(gradient, (160, 1))] * 3)
    art, cutout = cut_out(Image.fromarray(img))
    assert not cutout
    assert _ink_box(art) == (0, 0, 200, 160)


def test_nothing_but_background_stays_opaque() -> None:
    art, cutout = cut_out(Image.new("RGB", (64, 64), (255, 255, 255)))
    assert not cutout
    assert _ink_box(art) == (0, 0, 64, 64)


def test_raster_fits_the_box_keeping_aspect() -> None:
    fitted = _fit_raster(Image.new("RGB", (512, 704)), 300)
    assert fitted.size == (218, 300)
    assert fitted.mode == "RGBA"


def test_intrusion_is_placed_by_the_ink_not_the_rectangle() -> None:
    # the label says how far the ink crosses the safe line; a cut-out margin must not count
    from capstone.data.generate import plan_cases
    from capstone.data.real_art import render_real
    from capstone.src.schemas import IssueCode

    plan = next(
        p
        for p in plan_cases(200, seed=7, clean_fraction=0.0)
        if p.perturbations == (p.perturbations[0],) and p.has(IssueCode.CONTENT_IN_SAFE_ZONE)
    )
    art, _ = cut_out(_logo_on((250, 250, 250)))
    img, dpi, _ = render_real(plan, art, plan.seed)
    # the logo is (20, 20, 20); a dark caption may match too, but it sits inside the safe
    # area, so the smallest gap to the canvas edge is the logo's
    ink = np.asarray(img).max(axis=2) < 60
    ys, xs = np.nonzero(ink)
    h, w = ink.shape
    gap = min(xs.min(), ys.min(), w - 1 - xs.max(), h - 1 - ys.max())
    spec = plan.spec
    depth = plan.magnitude_for(IssueCode.CONTENT_IN_SAFE_ZONE)
    planned = (spec.bleed_in + spec.safe_zone_in * (1.0 - depth)) * dpi
    assert abs(gap - planned) <= 2


def test_an_uncut_pale_margin_is_not_counted_as_ink() -> None:
    # a busy-bordered image keeps its off-white field; on a white sticker it does not show
    from capstone.data.real_art import _visible_ink_box

    art = Image.new("RGBA", (200, 160), (246, 244, 240, 255))
    ImageDraw.Draw(art).rectangle((40, 30, 159, 129), fill=(20, 20, 20, 255))
    assert _visible_ink_box(art, (255, 255, 255)) == (40, 30, 160, 130)
    # on a navy sticker the same field is plainly visible
    assert _visible_ink_box(art, (29, 53, 87)) == (0, 0, 200, 160)
