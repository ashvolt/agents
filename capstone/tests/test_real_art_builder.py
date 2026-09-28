"""The real-art builder labels what it draws.

Needs the optional `data` dependencies (cairosvg); skipped without them.
"""

from __future__ import annotations

import pytest

pytest.importorskip("cairosvg")

from capstone.data.generate import delta_e76  # noqa: E402
from capstone.data.real_art import caption_colour  # noqa: E402


@pytest.mark.parametrize(
    "background",
    [(255, 255, 255), (29, 53, 87), (230, 57, 70), (42, 157, 143), (244, 162, 97)],
)
@pytest.mark.parametrize("target", [7.5, 13.6, 45.0])
def test_caption_colour_hits_the_target_on_any_background(background, target) -> None:  # noqa: ANN001
    # The generator's grey search could not get near small targets on coloured
    # backgrounds: "low-contrast" captions were legible and mislabelled.
    colour, achieved = caption_colour(background, target)
    assert achieved == pytest.approx(delta_e76(background, colour))
    assert abs(achieved - target) < 1.0
