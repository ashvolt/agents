"""L0 — Raw API.

Fill in every function below. Run `pytest levels/L0-raw-api -m "not integration"` to check
the free ones, then `pytest levels/L0-raw-api` to check the ones that hit the API.

Do not read solution/ until your attempt has been reviewed. The point of this level is to
end up able to estimate what a feature costs before you build it.
"""

from __future__ import annotations

from typing import Any

import anthropic

from shared.env import api_key, default_model

# You will need these. Import them yourself:
#     from shared.env import api_key, default_model

# --------------------------------------------------------------------------------------
# 1. Pricing
# --------------------------------------------------------------------------------------

# Fill this in: model ID -> (input $ per 1M tokens, output $ per 1M tokens).
#
# You need at least claude-opus-5, claude-sonnet-5, and claude-haiku-4-5. Look the numbers
# up rather than recalling them — pricing changes, and being wrong here means every cost
# estimate downstream is wrong.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0)
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollar cost of one call, from its token counts.

    Raise ValueError for a model that is not in MODEL_PRICING — silently returning 0.0 for
    an unknown model is how a cost dashboard ends up lying to you.
    """
    if model not in MODEL_PRICING:
        raise ValueError(f"Model {model} is not in MODEL_PRICING")

    input_price, output_price = MODEL_PRICING[model]
    input_cost = (input_tokens / 1_000_000) * input_price
    output_cost = (output_tokens / 1_000_000) * output_price
    return input_cost + output_cost


# --------------------------------------------------------------------------------------
# 2. Reading a response
# --------------------------------------------------------------------------------------


def summarize_stop(response: Any) -> str:
    """One human-readable line explaining why the model stopped.

    Map `response.stop_reason` as follows:

        "end_turn"   -> "completed normally"
        "max_tokens" -> "truncated at max_tokens"
        "tool_use"   -> "stopped to call a tool"
        "refusal"    -> "refused (<category>)"
        anything else -> "unhandled stop_reason: <value>"

    For a refusal, the category comes from `response.stop_details.category`. `stop_details`
    is populated *only* on a refusal and is None for every other stop reason, and the
    category itself can be None. Use "unknown" when you cannot get one.

    This function must never raise on a well-formed response. Getting the guard wrong here
    is the single most common way a working agent crashes in production.
    """
    reason = getattr(response, "stop_reason", None)
    if reason == "end_turn":
        return "completed normally"
    if reason == "max_tokens":
        return "truncated at max_tokens"
    if reason == "tool_use":
        return "stopped to call a tool"
    if reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) or "unknown"
        return f"refused ({category})"
    return f"unhandled stop_reason: {reason}"


# --------------------------------------------------------------------------------------
# 3. Talking to the API
# --------------------------------------------------------------------------------------


def make_client() -> Any:
    """Build and return an Anthropic client using the key from `api_key()`.

    Import the SDK inside this module, not inside the function.
    """
    raise NotImplementedError


def ask_raw(
    client: Any,
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
) -> Any:
    """Send one user message and return the full response object, not the text.

    `model=None` means use `default_model()`. `system=None` means send no system prompt at
    all — note that the system prompt is a top-level parameter, not a message with
    role="system".

    Returning the whole response is deliberate: usage, stop_reason and content all live on
    it, and the habit of throwing away everything but the text is what makes agents
    un-debuggable later.
    """
    raise NotImplementedError


def ask(client: Any, prompt: str, **kwargs: Any) -> str:
    """The text of the model's reply, as a plain string.

    Build this on `ask_raw`. The response content is a *list of blocks*, not a string —
    concatenate the text of the text blocks and ignore the rest.
    """
    raise NotImplementedError


def ask_with_cost(client: Any, prompt: str, **kwargs: Any) -> tuple[str, float]:
    """(reply text, dollar cost of the call).

    Read the token counts off `response.usage` and price them with `estimate_cost`. Use the
    model that was actually used, not the one you asked for.
    """
    raise NotImplementedError


def count_prompt_tokens(
    client: Any,
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
) -> int:
    """Token count of a prompt *before* sending it.

    There is an API endpoint for this. Do not install tiktoken and do not estimate from
    character count — neither matches Claude's tokenizer, and the whole point of this
    function is to be able to trust the number.
    """
    raise NotImplementedError


def stream_answer(
    client: Any,
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """Same result as `ask`, but streamed.

    Use the SDK's streaming helper and its "give me the finished message" method rather
    than hand-assembling text from raw events. Streaming matters for long outputs because
    a non-streaming request can hit the HTTP timeout before it finishes.
    """
    raise NotImplementedError


__all__ = [
    "MODEL_PRICING",
    "ask",
    "ask_raw",
    "ask_with_cost",
    "count_prompt_tokens",
    "estimate_cost",
    "make_client",
    "stream_answer",
    "summarize_stop",
]
