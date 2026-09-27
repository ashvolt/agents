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


def _antialiased_logo() -> Image.Image:
    # drawn at 4x and downsampled, so its edge has the in-between pixels a real image has
    big = Image.new("RGB", (800, 640), (255, 255, 255))
    ImageDraw.Draw(big).ellipse((200, 120, 600, 520), fill=(20, 20, 20))
    img = big.resize((200, 160), Image.Resampling.LANCZOS)
    ImageDraw.Draw(img).rectangle((10, 10, 11, 11), fill=(120, 120, 120))  # a 2 x 2 speck
    return img


def test_clean_edges_mattes_the_halo_and_drops_residue() -> None:
    raw, _ = cut_out(_antialiased_logo())
    clean, cutout = cut_out(_antialiased_logo(), clean_edges=True)
    assert cutout
    raw_a, clean_a = np.asarray(raw)[..., 3], np.asarray(clean)[..., 3]
    assert raw_a[10, 10] == 255 and clean_a[10, 10] == 0  # the speck is gone
    rgb = np.asarray(clean)[..., :3].max(axis=2)
    # a larger share of what is kept is solid ink: most pale in-between pixels were the halo
    assert (rgb[clean_a > 0] < 200).mean() > (
        np.asarray(raw)[..., :3].max(axis=2)[raw_a > 0] < 200
    ).mean()
    assert clean_a[80, 100] == 255  # the logo itself is kept


def test_clean_edges_is_off_by_default() -> None:
    a, _ = cut_out(_antialiased_logo())
    b, _ = cut_out(_antialiased_logo(), clean_edges=False)
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_stroke_label_records_the_drawn_pixel_width() -> None:
    from capstone.data.generate import plan_cases
    from capstone.data.real_art import drawn_perturbations, rule_stroke_px
    from capstone.src.schemas import IssueCode

    plans = [p for p in plan_cases(400, seed=11) if p.has(IssueCode.THIN_LINES)]
    assert plans
    for plan in plans:
        dpi = float(plan.spec.min_dpi)
        drawn_pt = rule_stroke_px(plan, dpi) / dpi * 72.0
        label = {p.code: p.magnitude for p in drawn_perturbations(plan, 1.0, drawn_stroke_dpi=dpi)}
        assert label[IssueCode.THIN_LINES] == round(plan.spec.min_stroke_pt / drawn_pt, 3)
        # off by default: the planned magnitude, as every earlier set was labelled
        legacy = {p.code: p.magnitude for p in drawn_perturbations(plan, 1.0)}
        assert legacy[IssueCode.THIN_LINES] == plan.magnitude_for(IssueCode.THIN_LINES)


def test_a_within_spec_stroke_that_rounds_under_the_limit_is_labelled_defective() -> None:
    # ai_art_v1: "0.9x" of a 0.5 pt minimum is 0.556 pt = 1.16 px at 150 dpi, drawn as 1 px
    from capstone.data.generate import plan_cases
    from capstone.data.real_art import drawn_perturbations
    from capstone.src.schemas import IssueCode

    plan = next(
        p
        for p in plan_cases(2000, seed=11)
        if p.has(IssueCode.THIN_LINES)
        and p.magnitude_for(IssueCode.THIN_LINES) == 0.9
        and p.spec.min_stroke_pt == 0.5
        and p.spec.min_dpi == 150
    )
    label = {p.code: p.magnitude for p in drawn_perturbations(plan, 1.0, drawn_stroke_dpi=150.0)}
    assert label[IssueCode.THIN_LINES] == round(0.5 / (1 / 150 * 72), 3) > 1.0
