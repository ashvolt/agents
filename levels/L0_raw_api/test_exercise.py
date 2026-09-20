"""The bar for L0. These are failing until exercise.py is filled in.

Free tests run with:      pytest levels/L0_raw_api -m "not integration"
Everything, costs money:  pytest levels/L0_raw_api
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from levels.L0_raw_api import exercise as ex
from shared.env import has_api_key

needs_api = pytest.mark.skipif(not has_api_key(), reason="no ANTHROPIC_API_KEY in .env")


# --------------------------------------------------------------------------------------
# Pricing — pure, free
# --------------------------------------------------------------------------------------

REQUIRED_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


@pytest.mark.parametrize("model", REQUIRED_MODELS)
def test_pricing_table_covers_required_models(model: str) -> None:
    assert model in ex.MODEL_PRICING, f"{model} missing from MODEL_PRICING"
    inp, out = ex.MODEL_PRICING[model]
    assert inp > 0 and out > 0
    assert out > inp, "output tokens cost more than input tokens on every Claude model"


def test_estimate_cost_one_million_each() -> None:
    # One million in and one million out should be exactly the table's two numbers summed.
    assert ex.MODEL_PRICING, "MODEL_PRICING is empty — this test would pass vacuously"
    for model, (inp, out) in ex.MODEL_PRICING.items():
        assert ex.estimate_cost(model, 1_000_000, 1_000_000) == pytest.approx(inp + out)


def test_estimate_cost_realistic_call() -> None:
    # 20K in, 2K out on Haiku 4.5 — one typical agent run from PLAN.md's budget table.
    assert ex.estimate_cost("claude-haiku-4-5", 20_000, 2_000) == pytest.approx(0.03)


def test_estimate_cost_zero_tokens_is_free() -> None:
    assert ex.estimate_cost("claude-opus-5", 0, 0) == 0.0


def test_estimate_cost_rejects_unknown_model() -> None:
    with pytest.raises(ValueError):
        ex.estimate_cost("claude-does-not-exist", 1000, 1000)


def test_opus_is_more_expensive_than_haiku() -> None:
    opus = ex.estimate_cost("claude-opus-5", 100_000, 10_000)
    haiku = ex.estimate_cost("claude-haiku-4-5", 100_000, 10_000)
    assert opus > haiku


# --------------------------------------------------------------------------------------
# Stop reasons — pure, free
# --------------------------------------------------------------------------------------


def fake_response(stop_reason: str, stop_details: object = None) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, stop_details=stop_details)


def test_summarize_stop_end_turn() -> None:
    assert ex.summarize_stop(fake_response("end_turn")) == "completed normally"


def test_summarize_stop_max_tokens() -> None:
    assert ex.summarize_stop(fake_response("max_tokens")) == "truncated at max_tokens"


def test_summarize_stop_tool_use() -> None:
    assert ex.summarize_stop(fake_response("tool_use")) == "stopped to call a tool"


def test_summarize_stop_refusal_with_category() -> None:
    resp = fake_response("refusal", SimpleNamespace(category="cyber", explanation="nope"))
    assert ex.summarize_stop(resp) == "refused (cyber)"


def test_summarize_stop_refusal_without_details() -> None:
    # stop_details can be absent even on a refusal. Must not raise.
    assert ex.summarize_stop(fake_response("refusal")) == "refused (unknown)"


def test_summarize_stop_refusal_with_null_category() -> None:
    resp = fake_response("refusal", SimpleNamespace(category=None, explanation=None))
    assert ex.summarize_stop(resp) == "refused (unknown)"


def test_summarize_stop_never_raises_on_other_reasons() -> None:
    # stop_details is None here — the guard has to hold for every non-refusal reason.
    for reason in ["pause_turn", "something_new_in_2027"]:
        assert reason in ex.summarize_stop(fake_response(reason))


# --------------------------------------------------------------------------------------
# The API — costs money
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():  # noqa: ANN201
    return ex.make_client()


@needs_api
@pytest.mark.integration
def test_ask_returns_a_string(client) -> None:  # noqa: ANN001
    reply = ex.ask(client, "Reply with exactly the word: pong")
    assert isinstance(reply, str)
    assert "pong" in reply.lower()


@needs_api
@pytest.mark.integration
def test_system_prompt_changes_behaviour(client) -> None:  # noqa: ANN001
    reply = ex.ask(
        client,
        "What is 2 + 2?",
        system="You always answer in French, using words only, never digits.",
    )
    assert "quatre" in reply.lower()


@needs_api
@pytest.mark.integration
def test_ask_raw_exposes_usage_and_stop_reason(client) -> None:  # noqa: ANN001
    resp = ex.ask_raw(client, "Say hi.")
    assert resp.usage.input_tokens > 0
    assert resp.usage.output_tokens > 0
    assert ex.summarize_stop(resp) == "completed normally"


@needs_api
@pytest.mark.integration
def test_max_tokens_truncation_is_visible(client) -> None:  # noqa: ANN001
    # A long answer with a tiny cap: the model gets cut off and says so in stop_reason.
    resp = ex.ask_raw(client, "Write a 500 word essay about glue.", max_tokens=16)
    assert ex.summarize_stop(resp) == "truncated at max_tokens"


@needs_api
@pytest.mark.integration
def test_ask_with_cost_returns_a_plausible_price(client) -> None:  # noqa: ANN001
    reply, cost = ex.ask_with_cost(client, "Reply with exactly the word: pong")
    assert "pong" in reply.lower()
    assert 0 < cost < 0.05, f"one tiny call should not cost ${cost}"


@needs_api
@pytest.mark.integration
def test_count_prompt_tokens_scales_with_prompt_size(client) -> None:  # noqa: ANN001
    small = ex.count_prompt_tokens(client, "hi")
    large = ex.count_prompt_tokens(client, "hi " * 500)
    assert small > 0
    assert large > small * 10


@needs_api
@pytest.mark.integration
def test_system_prompt_counts_toward_input_tokens(client) -> None:  # noqa: ANN001
    without = ex.count_prompt_tokens(client, "hi")
    with_system = ex.count_prompt_tokens(client, "hi", system="You are a helpful " * 100)
    assert with_system > without


@needs_api
@pytest.mark.integration
def test_stream_answer_matches_ask(client) -> None:  # noqa: ANN001
    streamed = ex.stream_answer(client, "Reply with exactly the word: pong")
    assert isinstance(streamed, str)
    assert "pong" in streamed.lower()
