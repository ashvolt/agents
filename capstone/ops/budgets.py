"""Per-file budgets.

Constitution Principle VI: budgets are part of the loop, not a wrapper around it. The
loop asks the budget before every model call and every tool call, so exhaustion is a
designed outcome rather than a timeout somewhere up the stack.

Principle IV decides what that outcome is: hitting a cap routes to ESCALATE. A file the
system ran out of budget on has not been checked, and an unchecked file is never approved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Defaults sized against SC-003 ($0.01/file) and SC-004 (p95 <= 20s). They are caps, not
# expectations: a typical run should finish well inside all four.
DEFAULT_MAX_STEPS = 8
DEFAULT_MAX_TOKENS = 60_000
DEFAULT_MAX_SECONDS = 45.0
DEFAULT_MAX_COST_USD = 0.05


class BudgetExhausted(Exception):
    """Raised when a cap is hit. Carries which one, so the trace can record it."""

    def __init__(self, which: str, limit: float, used: float) -> None:
        super().__init__(f"budget exhausted: {which} (used {used:g} of {limit:g})")
        self.which = which
        self.limit = limit
        self.used = used


@dataclass
class Budget:
    """Caps for one file, plus the running totals."""

    max_steps: int = DEFAULT_MAX_STEPS
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_seconds: float = DEFAULT_MAX_SECONDS
    max_cost_usd: float = DEFAULT_MAX_COST_USD

    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    started_at: float = field(default_factory=time.perf_counter)

    @property
    def elapsed_s(self) -> float:
        return time.perf_counter() - self.started_at

    def check(self) -> None:
        """Raise if any cap is already breached. Called before spending, not after."""
        if self.steps >= self.max_steps:
            raise BudgetExhausted("steps", self.max_steps, self.steps)
        if self.tokens >= self.max_tokens:
            raise BudgetExhausted("tokens", self.max_tokens, self.tokens)
        if self.elapsed_s >= self.max_seconds:
            raise BudgetExhausted("wall_clock_s", self.max_seconds, round(self.elapsed_s, 2))
        if self.cost_usd >= self.max_cost_usd:
            raise BudgetExhausted("cost_usd", self.max_cost_usd, round(self.cost_usd, 6))

    def charge(self, *, tokens: int = 0, cost_usd: float = 0.0, steps: int = 1) -> None:
        self.steps += steps
        self.tokens += tokens
        self.cost_usd += cost_usd

    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps)

    def summary(self) -> dict[str, float | int]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "elapsed_s": round(self.elapsed_s, 3),
        }


__all__ = [
    "DEFAULT_MAX_COST_USD",
    "DEFAULT_MAX_SECONDS",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_MAX_TOKENS",
    "Budget",
    "BudgetExhausted",
]
