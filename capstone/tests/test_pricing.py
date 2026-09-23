"""Cost accounting.

These exist because of a real bug: the API returns the dated snapshot it served
(`claude-haiku-4-5-20251001`) for a request naming `claude-haiku-4-5`, the pricing table
had only the undated id, and the lookup failed. The agent caught the exception and
reported **$0.00 per file** across a whole sweep.

Two things that made that worse than a wrong number:

1. It looked like a free run rather than an unmeasured one.
2. The sweep spend cap reads the same figure, so a budget guard was silently disabled.

Offline. No API, no spend.
"""

from __future__ import annotations

import pytest

from capstone.ops.pricing import (
    BATCH_DISCOUNT,
    CACHE_READ_MULTIPLIER,
    MODEL_PRICING,
    UnknownModelError,
    estimate_cost,
    image_tokens,
    resolve_model,
)

REQUIRED = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


@pytest.mark.parametrize("model", REQUIRED)
def test_table_covers_the_models_we_use(model: str) -> None:
    assert model in MODEL_PRICING
    inp, out = MODEL_PRICING[model]
    assert 0 < inp < out, "output always costs more than input"


# --------------------------------------------------------------------------------------
# The regression
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("served", "expected"),
    [
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-haiku-4-5", "claude-haiku-4-5"),
        ("claude-opus-5-20260101", "claude-opus-5"),
        ("claude-sonnet-5", "claude-sonnet-5"),
    ],
)
def test_dated_snapshot_ids_resolve(served: str, expected: str) -> None:
    assert resolve_model(served) == expected


def test_dated_snapshot_is_priced_not_zeroed() -> None:
    # The exact shape of the bug: a real served id must produce a real number.
    cost = estimate_cost("claude-haiku-4-5-20251001", 5_000, 300)
    assert cost > 0
    assert cost == estimate_cost("claude-haiku-4-5", 5_000, 300)


def test_genuinely_unknown_model_still_raises() -> None:
    # The fix must not become "resolve anything". An unknown model is still an error;
    # a wrong price is quieter than no price and therefore worse.
    with pytest.raises(UnknownModelError, match="no pricing"):
        resolve_model("claude-does-not-exist-9")


def test_unknown_model_error_names_what_it_tried() -> None:
    with pytest.raises(UnknownModelError, match="tried"):
        resolve_model("claude-imaginary-2-20260101")


def test_a_bare_date_suffix_is_not_enough_to_invent_a_model() -> None:
    with pytest.raises(UnknownModelError):
        estimate_cost("gpt-4-20240101", 100, 100)


# --------------------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------------------


def test_one_million_each_is_the_table_row_summed() -> None:
    for model, (inp, out) in MODEL_PRICING.items():
        assert estimate_cost(model, 1_000_000, 1_000_000) == pytest.approx(inp + out)


def test_zero_tokens_is_free() -> None:
    assert estimate_cost("claude-haiku-4-5", 0, 0) == 0.0


def test_cached_reads_are_cheaper_than_fresh_input() -> None:
    fresh = estimate_cost("claude-haiku-4-5", 10_000, 0)
    cached = estimate_cost("claude-haiku-4-5", 0, 0, cache_read_tokens=10_000)
    assert cached == pytest.approx(fresh * CACHE_READ_MULTIPLIER)


def test_batch_halves_the_bill() -> None:
    full = estimate_cost("claude-haiku-4-5", 10_000, 1_000)
    batched = estimate_cost("claude-haiku-4-5", 10_000, 1_000, batch=True)
    assert batched == pytest.approx(full * BATCH_DISCOUNT)


def test_cache_reads_are_counted_separately_from_input() -> None:
    # input_tokens from the API is the UNCACHED portion only. Adding the two together
    # overstates a well-cached run by roughly 10x.
    split = estimate_cost("claude-haiku-4-5", 1_000, 0, cache_read_tokens=9_000)
    lumped = estimate_cost("claude-haiku-4-5", 10_000, 0)
    assert split < lumped


def test_opus_costs_more_than_haiku() -> None:
    assert estimate_cost("claude-opus-5", 10_000, 1_000) > estimate_cost(
        "claude-haiku-4-5", 10_000, 1_000
    )


# --------------------------------------------------------------------------------------
# Image tokens
# --------------------------------------------------------------------------------------


def test_image_tokens_scale_with_area() -> None:
    assert image_tokens(1000, 1000) == pytest.approx(image_tokens(500, 500) * 4, rel=0.01)


def test_image_tokens_never_zero() -> None:
    assert image_tokens(1, 1) >= 1
