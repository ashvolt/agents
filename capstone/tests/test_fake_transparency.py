"""FAKE_TRANSPARENCY: a transparency checkerboard painted into the pixels.

AI image tools draw one when asked for a transparent background, and it prints as a grid
of grey squares. The check must find it, including after JPEG, and must not fire on
checkered *design*: dark (racing flag), coloured (gingham) or small.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from capstone.src.schemas import IssueCode
from capstone.tools.bucket2_pixels import check_fake_transparency, measure_checkerboard

W, H = 900, 900


def _checker(
    size: tuple[int, int] = (W, H),
    cell: int = 16,
    light: tuple[int, int, int] = (255, 255, 255),
    dark: tuple[int, int, int] = (204, 204, 204),
    box: tuple[int, int, int, int] | None = None,
    base: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    img = Image.new("RGB", size, base)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box or (0, 0, *size)
    for y in range(y0, y1, cell):
        for x in range(x0, x1, cell):
            colour = light if ((x - x0) // cell + (y - y0) // cell) % 2 == 0 else dark
            d.rectangle((x, y, min(x + cell, x1) - 1, min(y + cell, y1) - 1), fill=colour)
    return img


def _with_art(img: Image.Image) -> Image.Image:
    d = ImageDraw.Draw(img)
    d.ellipse((250, 250, 650, 650), fill=(214, 91, 60), outline=(20, 20, 20), width=8)
    return img


def _jpeg(img: Image.Image, quality: int = 75) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _codes(img: Image.Image) -> set[IssueCode]:
    return {i.code for i in check_fake_transparency(img)}


def test_painted_checkerboard_behind_art_is_found() -> None:
    assert _codes(_with_art(_checker())) == {IssueCode.FAKE_TRANSPARENCY}


def test_found_after_jpeg_and_at_other_cell_sizes() -> None:
    for cell in (8, 24, 40):
        img = _jpeg(_with_art(_checker(cell=cell, light=(238, 238, 238), dark=(191, 191, 191))))
        assert _codes(img) == {IssueCode.FAKE_TRANSPARENCY}, cell


def test_plain_white_background_is_not_a_checkerboard() -> None:
    assert _codes(_with_art(Image.new("RGB", (W, H), "white"))) == set()


def test_racing_flag_is_design_not_transparency() -> None:
    assert _codes(_checker(light=(250, 250, 250), dark=(15, 15, 15), cell=40)) == set()


def test_coloured_gingham_is_design_not_transparency() -> None:
    assert _codes(_checker(light=(255, 255, 255), dark=(230, 120, 120), cell=30)) == set()


def test_a_small_checkered_patch_is_not_a_background() -> None:
    img = _checker(box=(40, 40, 200, 200))  # ~3% of the image
    assert _codes(img) == set()


def test_measurement_reports_cell_size() -> None:
    found = measure_checkerboard(_checker(cell=20))
    assert found is not None
    assert abs(found[2] - 20) <= 2
