"""Token pricing and per-call cost.

Constitution Principle VI: cost is a first-class metric, so this is a real module with
tests rather than a number inlined at the call site. The lesson it comes from is
levels/L0_raw_api — an unknown model raises instead of silently returning 0.0, because a
cost dashboard that reports free is worse than no dashboard.

Prices are USD per million tokens and are checked against the published rates. They
change; when they do, this table is the one place to edit.
"""

from __future__ import annotations

# model id -> (input $/MTok, output $/MTok)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Cached input reads are billed at roughly a tenth of normal input; writing to the cache
# costs about 1.25x. research.md D-7 and D-9: at ~20K input tokens per file these two
# multipliers are what decide whether SC-003 is reachable at all.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25

# Non-interactive sweeps can go through the Batch API at half price.
BATCH_DISCOUNT = 0.50


class UnknownModelError(ValueError):
    """Raised for a model with no pricing entry. Never returns 0.0 instead."""


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    """Dollar cost of one call.

    `input_tokens` is the uncached portion only — the API reports cached reads separately
    and they are billed differently, so adding them together overstates the bill by
    roughly 10x on a well-cached run.
    """
    try:
        in_rate, out_rate = MODEL_PRICING[model]
    except KeyError:
        known = ", ".join(sorted(MODEL_PRICING))
        raise UnknownModelError(
            f"no pricing for model {model!r}. Known: {known}. "
            "Returning 0.0 here would make every cost report a lie."
        ) from None

    cost = (
        input_tokens / 1_000_000 * in_rate
        + output_tokens / 1_000_000 * out_rate
        + cache_read_tokens / 1_000_000 * in_rate * CACHE_READ_MULTIPLIER
        + cache_write_tokens / 1_000_000 * in_rate * CACHE_WRITE_MULTIPLIER
    )
    return cost * (BATCH_DISCOUNT if batch else 1.0)


def image_tokens(width_px: int, height_px: int) -> int:
    """Approximate token cost of an image.

    The documented approximation is (width x height) / 750. It is an estimate; the
    measured number comes from `messages.count_tokens` and replaces this wherever the
    real figure matters (spec.md OQ-1).
    """
    return max(1, round(width_px * height_px / 750))


__all__ = [
    "BATCH_DISCOUNT",
    "CACHE_READ_MULTIPLIER",
    "CACHE_WRITE_MULTIPLIER",
    "MODEL_PRICING",
    "UnknownModelError",
    "estimate_cost",
    "image_tokens",
]
