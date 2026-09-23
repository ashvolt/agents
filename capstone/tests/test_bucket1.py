"""Bucket 1 — metadata checks.

Every fixture here is built with PIL directly rather than through
`capstone/data/generate.py`. That is deliberate: research.md D-4 warns that a generator
and a checker sharing assumptions grade each other, and a test suite that reuses the
generator inherits exactly that blind spot. These construct the file conditions by hand.

Borderline coverage is the point. A check that only sees obvious cases never reveals an
off-by-one in unit conversion or an inclusive/exclusive boundary bug, and borderline files
are where false approves come from.

Offline. No API, no spend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from capstone.src.product_specs import get_spec
from capstone.src.schemas import IssueCode, OrderMetadata
from capstone.tools.bucket1_metadata import (
    check_aspect,
    check_bleed,
    check_color_mode,
    check_resolution,
    effective_dpi,
    inspect_file,
    read_metadata,
)

SPEC = get_spec("die-cut-sticker")  # min_dpi 150, bleed 0.125, aspect_tolerance 0.02


def order(width: float = 2.0, height: float = 2.0) -> OrderMetadata:
    return OrderMetadata(
        order_id="o1", product_id="die-cut-sticker", width_in=width, height_in=height, quantity=100
    )


def write_art(
    path: Path,
    *,
    order_w: float = 2.0,
    order_h: float = 2.0,
    dpi: float = 150.0,
    bleed_in: float | None = None,
    width_scale: float = 1.0,
    mode: str = "CMYK",
) -> Path:
    """Build a file with known physical size, resolution and colour mode.

    Canvas is trim + bleed on every edge, which is what a correct print file is. Pixel
    dimensions follow from the physical size and the DPI, so the two stay consistent
    unless a test deliberately breaks one.
    """
    bleed = SPEC.bleed_in if bleed_in is None else bleed_in
    canvas_w_in = order_w + 2 * bleed
    canvas_h_in = order_h + 2 * bleed
    px_w = max(8, round(canvas_w_in * dpi * width_scale))
    px_h = max(8, round(canvas_h_in * dpi))

    img = Image.new("RGB", (px_w, px_h), (240, 238, 234))
    suffix = ".png" if mode in ("RGB", "RGBA") else ".tif"
    path = path.with_suffix(suffix)
    img.convert(mode).save(path, dpi=(dpi, dpi))
    return path


def codes(issues) -> set[IssueCode]:  # noqa: ANN001
    return {i.code for i in issues}


# --------------------------------------------------------------------------------------
# Readability — a corrupt upload must never raise
# --------------------------------------------------------------------------------------


def test_missing_file_reports_unreadable(tmp_path: Path) -> None:
    meta = read_metadata(tmp_path / "nope.png")
    assert not meta.ok
    issues, _ = inspect_file(tmp_path / "nope.png", SPEC, order())
    assert codes(issues) == {IssueCode.UNREADABLE_FILE}


def test_empty_file_reports_unreadable(tmp_path: Path) -> None:
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    assert not read_metadata(p).ok


def test_truncated_file_reports_unreadable(tmp_path: Path) -> None:
    p = write_art(tmp_path / "art", mode="RGB")
    raw = p.read_bytes()
    p.write_bytes(raw[: len(raw) // 2])
    assert not read_metadata(p).ok


def test_unsupported_extension_reports_unreadable(tmp_path: Path) -> None:
    p = tmp_path / "art.psd"
    p.write_bytes(b"8BPS" + b"\x00" * 64)
    assert not read_metadata(p).ok


def test_unreadable_short_circuits_other_checks(tmp_path: Path) -> None:
    # One root cause should not become five derived findings in the escalation queue.
    p = tmp_path / "broken.png"
    p.write_bytes(b"not an image")
    issues, _ = inspect_file(p, SPEC, order())
    assert codes(issues) == {IssueCode.UNREADABLE_FILE}


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("a.png", b"\x89PNG\r\n"),
        ("b.tif", b"II*\x00junk"),
        ("c.jpg", b"\xff\xd8"),
    ],
)
def test_read_metadata_never_raises_on_garbage(
    tmp_path: Path, name: str, payload: bytes
) -> None:
    p = tmp_path / name
    p.write_bytes(payload)
    meta = read_metadata(p)
    assert not meta.ok
    assert meta.error


# --------------------------------------------------------------------------------------
# Colour mode
# --------------------------------------------------------------------------------------


def test_cmyk_is_accepted(tmp_path: Path) -> None:
    p = write_art(tmp_path / "a", mode="CMYK")
    assert check_color_mode(read_metadata(p), SPEC) == []


def test_rgb_is_rejected_for_a_cmyk_product(tmp_path: Path) -> None:
    p = write_art(tmp_path / "a", mode="RGB")
    assert codes(check_color_mode(read_metadata(p), SPEC)) == {IssueCode.WRONG_COLOR_MODE}


def test_rgba_is_reported_as_rgb_family(tmp_path: Path) -> None:
    p = write_art(tmp_path / "a", mode="RGBA")
    issues = check_color_mode(read_metadata(p), SPEC)
    assert issues and "RGB" in issues[0].evidence.note


# --------------------------------------------------------------------------------------
# Resolution — straddling the 150 DPI limit
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dpi", "should_flag"),
    [
        (300.0, False),   # 2.0x — comfortably fine
        (166.0, False),   # 1.1x — just inside
        (151.0, False),   # barely inside
        (150.0, False),   # exactly at the limit is acceptable
        (148.0, True),    # 0.99x — just outside
        (136.0, True),    # 0.9x  — borderline defect
        (75.0, True),     # 0.5x  — obvious
    ],
)
def test_resolution_boundary(tmp_path: Path, dpi: float, should_flag: bool) -> None:
    p = write_art(tmp_path / "a", dpi=dpi)
    issues = check_resolution(read_metadata(p), SPEC, order())
    assert bool(issues) is should_flag, f"{dpi} dpi against a {SPEC.min_dpi} minimum"


def test_resolution_evidence_carries_both_numbers(tmp_path: Path) -> None:
    p = write_art(tmp_path / "a", dpi=75.0)
    issue = check_resolution(read_metadata(p), SPEC, order())[0]
    assert issue.evidence.measured == pytest.approx(75, abs=1)
    assert issue.evidence.required == 150
    assert issue.evidence.unit == "dpi"


def test_low_resolution_does_not_also_trip_bleed(tmp_path: Path) -> None:
    # The decoupling that cost a rewrite: a file scaled down keeps its physical size
    # because the declared DPI drops with the pixels. Only sharpness suffers.
    p = write_art(tmp_path / "a", dpi=75.0)
    issues, _ = inspect_file(p, SPEC, order())
    assert IssueCode.LOW_RESOLUTION in codes(issues)
    assert IssueCode.MISSING_BLEED not in codes(issues)


def test_resolution_falls_back_when_dpi_metadata_is_absent(tmp_path: Path) -> None:
    # A file with no resolution metadata gets scaled to fit, so pixels over the required
    # canvas is exactly what it will resolve to.
    p = tmp_path / "nodpi.png"
    Image.new("RGB", (100, 100), "white").save(p)  # no dpi= argument
    meta = read_metadata(p)
    assert meta.declared_dpi is None
    assert effective_dpi(meta, SPEC, order()) == pytest.approx(100 / 2.25, rel=0.01)


# --------------------------------------------------------------------------------------
# Bleed — straddling 0.125 in
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bleed", "should_flag"),
    [
        (0.250, False),   # 2x — extra is trimmed away, not a defect
        (0.138, False),   # 1.1x
        (0.125, False),   # exactly required
        (0.1235, False),  # inside the 2% epsilon — metadata noise
        (0.1125, True),   # 0.9x — borderline defect
        (0.0625, True),   # 0.5x
        (0.0, True),      # none at all
    ],
)
def test_bleed_boundary(tmp_path: Path, bleed: float, should_flag: bool) -> None:
    p = write_art(tmp_path / "a", bleed_in=bleed)
    issues = check_bleed(read_metadata(p), SPEC, order())
    assert bool(issues) is should_flag, f"{bleed} in against a {SPEC.bleed_in} requirement"


def test_oversize_artwork_is_not_a_bleed_defect(tmp_path: Path) -> None:
    p = write_art(tmp_path / "a", bleed_in=0.5)
    assert check_bleed(read_metadata(p), SPEC, order()) == []


def test_bleed_evidence_reports_bleed_not_sheet_size(tmp_path: Path) -> None:
    # Measuring bleed directly is why a 10% shortfall on a 2in sticker is visible at all:
    # as a fraction of the whole sheet it is about 1%.
    p = write_art(tmp_path / "a", bleed_in=0.0625)
    issue = check_bleed(read_metadata(p), SPEC, order())[0]
    assert issue.evidence.measured == pytest.approx(0.0625, abs=0.002)
    assert issue.evidence.required == 0.125
    assert issue.evidence.unit == "in"


# --------------------------------------------------------------------------------------
# Aspect — measured against the CANVAS ratio, not the trim ratio
# --------------------------------------------------------------------------------------


def test_correct_canvas_aspect_passes_on_a_non_square_order(tmp_path: Path) -> None:
    # The bug that produced 15 false positives: a 4x2 order with 0.125 bleed wants a
    # 4.25x2.25 canvas, which is 1.889:1 — not the 2:1 of the trim.
    p = write_art(tmp_path / "a", order_w=4.0, order_h=2.0)
    assert check_aspect(read_metadata(p), SPEC, order(4.0, 2.0)) == []


@pytest.mark.parametrize(
    ("scale", "should_flag"),
    [(1.0, False), (1.015, False), (1.03, True), (1.10, True)],
)
def test_aspect_boundary(tmp_path: Path, scale: float, should_flag: bool) -> None:
    p = write_art(tmp_path / "a", order_w=4.0, order_h=2.0, width_scale=scale)
    issues = check_aspect(read_metadata(p), SPEC, order(4.0, 2.0))
    assert bool(issues) is should_flag


def test_aspect_is_suppressed_when_bleed_is_missing(tmp_path: Path) -> None:
    # Without a trim box the two are entangled: a file short on bleed has a different
    # canvas ratio purely because of the missing margin. Reporting both would tell the
    # customer to fix a proportion problem they do not have.
    p = write_art(tmp_path / "a", order_w=4.0, order_h=2.0, bleed_in=0.03)
    issues, _ = inspect_file(p, SPEC, order(4.0, 2.0))
    assert IssueCode.MISSING_BLEED in codes(issues)
    assert IssueCode.ASPECT_MISMATCH not in codes(issues)


def test_aspect_reported_once_bleed_is_correct(tmp_path: Path) -> None:
    # ...and it surfaces on resubmission, which is the behaviour that suppression buys.
    p = write_art(tmp_path / "a", order_w=4.0, order_h=2.0, width_scale=1.10)
    issues, _ = inspect_file(p, SPEC, order(4.0, 2.0))
    assert IssueCode.ASPECT_MISMATCH in codes(issues)


# --------------------------------------------------------------------------------------
# A clean file produces nothing
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("product_id", ["die-cut-sticker", "roll-label", "custom-magnet"])
def test_a_correct_file_reports_no_issues(tmp_path: Path, product_id: str) -> None:
    spec = get_spec(product_id)
    o = OrderMetadata(
        order_id="o", product_id=product_id, width_in=3.0, height_in=2.0, quantity=50
    )
    canvas_w = o.width_in + 2 * spec.bleed_in
    canvas_h = o.height_in + 2 * spec.bleed_in
    p = tmp_path / f"{product_id}.tif"
    Image.new("RGB", (round(canvas_w * spec.min_dpi), round(canvas_h * spec.min_dpi))).convert(
        "CMYK"
    ).save(p, dpi=(spec.min_dpi, spec.min_dpi))

    issues, meta = inspect_file(p, spec, o)
    assert meta.ok
    assert issues == [], f"clean file flagged: {codes(issues)}"
