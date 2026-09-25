"""The false-approve upper bound is exact (Clopper-Pearson), not a normal approximation.

Reference values from scipy.stats.beta.ppf(0.95, k + 1, n - k).
"""

from __future__ import annotations

import pytest

from capstone.evals.metrics import clopper_pearson_upper


@pytest.mark.parametrize(
    ("k", "n", "expected"),
    [
        (0, 300, 0.00994),  # the rule of three, ~3/n
        (1, 480, 0.00984),
        (2, 479, 0.01308),  # real_art_v4: the normal approximation said 1.0%
        (3, 218, 0.03518),
        (14, 227, 0.09474),
    ],
)
def test_matches_reference(k: int, n: int, expected: float) -> None:
    assert clopper_pearson_upper(k, n) == pytest.approx(expected, abs=1e-5)


def test_edges() -> None:
    assert clopper_pearson_upper(0, 0) == 1.0
    assert clopper_pearson_upper(5, 5) == 1.0
    assert clopper_pearson_upper(0, 1_000_000) < 1e-5
