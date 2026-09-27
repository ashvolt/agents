"""The generator's aspect labels must agree with what check_aspect measures.

real_art_v3 case-00425 was labelled ASPECT_MISMATCH 1.1 but measured 0.3x the tolerance:
the stretch was applied to a canvas that already had 2x (in-spec) bleed. A false approve
on a mislabelled file is a label error, and it had been counted against the pipeline.
"""

from __future__ import annotations

import pytest

from capstone.data.generate import CasePlan, render
from capstone.src.product_specs import get_spec
from capstone.src.schemas import IssueCode, OrderMetadata, Perturbation, Split

SPEC = get_spec("custom-magnet")
ORDER = OrderMetadata(
    order_id="ORD-T", product_id=SPEC.product_id, width_in=5.0, height_in=3.0, quantity=1
)


def _measured_aspect_ratio(perts: tuple[Perturbation, ...]) -> float:
    plan = CasePlan("t", SPEC, ORDER, perts, Split.TRAIN, seed=1)
    img, _dpi = render(plan)
    w, h = img.size
    expected = (ORDER.width_in + 2 * SPEC.bleed_in) / (ORDER.height_in + 2 * SPEC.bleed_in)
    return abs((w / h) / expected - 1.0) / SPEC.aspect_tolerance


@pytest.mark.parametrize("bleed", [None, 0.5, 0.9])
@pytest.mark.parametrize("aspect", [0.5, 0.9, 1.1, 2.0])
def test_aspect_label_matches_measurement(bleed: float | None, aspect: float) -> None:
    perts = (Perturbation(code=IssueCode.ASPECT_MISMATCH, magnitude=aspect),)
    if bleed is not None:
        perts += (Perturbation(code=IssueCode.MISSING_BLEED, magnitude=bleed),)
    measured = _measured_aspect_ratio(perts)
    assert measured == pytest.approx(aspect, abs=0.05)
    assert (measured > 1.0) == (aspect > 1.0)
