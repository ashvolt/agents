"""Principle IV proof: no failure produces an APPROVE.

Every one of these injects a different way the run can go wrong and asserts the same
thing. They are the reason `finalize()` is a chokepoint rather than a convention — a
convention survives until someone adds an `except` with a default, and these tests fail
the moment that happens.

Fully offline. A fake client stands in for the API, so refusals, truncation, garbage
output and outright crashes are all reachable without spending anything or waiting for a
model to misbehave on its own schedule.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from capstone.ops.budgets import Budget
from capstone.src.agent import (
    CONFIDENCE_FLOOR,
    PreflightAgent,
    finalize,
    parse_verdict,
)
from capstone.src.schemas import (
    EscalationReason,
    Evidence,
    Issue,
    IssueCode,
    OrderMetadata,
    PreflightCase,
    Severity,
    VerdictType,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------------------


class FakeUsage(SimpleNamespace):
    pass


def fake_response(
    *,
    stop_reason: str = "tool_use",
    content: list | None = None,
    model: str = "claude-haiku-4-5",
) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content or [],
        model=model,
        usage=FakeUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


class ScriptedClient:
    """Returns a queued response per call; raises if the loop asks for more than scripted."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls += 1
        if not self._responses:
            raise AssertionError("loop made more calls than the script provides")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def case(tmp_path: Path) -> PreflightCase:
    from PIL import Image

    p = tmp_path / "art.png"
    Image.new("RGB", (400, 400), (240, 240, 235)).save(p, dpi=(150, 150))
    return PreflightCase(
        case_id="c1",
        image_path=p,
        order=OrderMetadata(
            order_id="o1", product_id="die-cut-sticker", width_in=2, height_in=2, quantity=100
        ),
    )


def agent_with(responses: list, *, use_tools: bool = True) -> PreflightAgent:
    return PreflightAgent(
        client=ScriptedClient(responses), model="claude-haiku-4-5", use_tools=use_tools
    )


APPROVE_PAYLOAD = {"verdict": "APPROVE", "confidence": 0.99, "issues": []}


# --------------------------------------------------------------------------------------
# The invariant, one failure mode at a time
# --------------------------------------------------------------------------------------


def test_refusal_escalates(case: PreflightCase) -> None:
    agent = agent_with([fake_response(stop_reason="refusal")])
    verdict, trace = agent.triage(case)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.REFUSAL
    assert trace.termination == "refusal"


def test_truncated_response_escalates_and_is_never_parsed(case: PreflightCase) -> None:
    # The model was mid-way through submitting an APPROVE when it hit the cap. Parsing a
    # truncated generation is how a half-written verdict becomes a bad print.
    agent = agent_with(
        [
            fake_response(
                stop_reason="max_tokens",
                content=[tool_use_block("submit_verdict", APPROVE_PAYLOAD)],
            )
        ]
    )
    verdict, trace = agent.triage(case)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.TRUNCATED_RESPONSE
    assert trace.termination == "truncated"


def test_api_error_escalates(case: PreflightCase) -> None:
    import anthropic

    err = anthropic.APIConnectionError(request=SimpleNamespace())  # type: ignore[arg-type]
    agent = agent_with([err])
    verdict, _ = agent.triage(case)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.MODEL_ERROR


def test_prose_instead_of_verdict_gets_one_repair_then_escalates(case: PreflightCase) -> None:
    prose = fake_response(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="Looks fine to me!")],
    )
    agent = agent_with([prose, prose])
    verdict, trace = agent.triage(case)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.SCHEMA_INVALID
    assert trace.termination == "no_verdict"
    assert agent.client.calls == 2, "should retry exactly once before giving up"


def test_unknown_product_escalates(tmp_path: Path) -> None:
    from PIL import Image

    p = tmp_path / "a.png"
    Image.new("RGB", (100, 100)).save(p)
    bad = PreflightCase(
        case_id="c",
        image_path=p,
        order=OrderMetadata(
            order_id="o", product_id="unobtainium", width_in=2, height_in=2, quantity=1
        ),
    )
    agent = agent_with([])
    verdict, trace = agent.triage(bad)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.UNSUPPORTED_INPUT
    assert agent.client.calls == 0, "must not spend a model call on an unknown product"


def test_undecodable_file_escalates(tmp_path: Path) -> None:
    p = tmp_path / "broken.png"
    p.write_bytes(b"not a png")
    broken = PreflightCase(
        case_id="c",
        image_path=p,
        order=OrderMetadata(
            order_id="o", product_id="die-cut-sticker", width_in=2, height_in=2, quantity=1
        ),
    )
    agent = agent_with([])
    verdict, _ = agent.triage(broken)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.UNSUPPORTED_INPUT


def test_budget_exhaustion_escalates(case: PreflightCase, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the constructor the agent calls. Setting Budget.max_steps directly does not
    # work: the dataclass captured DEFAULT_MAX_STEPS into __init__ at class creation, so
    # instances keep the original default no matter what the class attribute says.
    monkeypatch.setattr("capstone.src.agent.Budget", lambda: Budget(max_steps=2))

    looping = fake_response(
        content=[tool_use_block("get_product_spec", {"product_id": "die-cut-sticker"})]
    )
    agent = agent_with([looping] * 6)
    verdict, trace = agent.triage(case)
    assert verdict.verdict is VerdictType.ESCALATE
    assert verdict.escalation_reason is EscalationReason.BUDGET_EXHAUSTED
    assert trace.termination.startswith("budget_")
    assert agent.client.calls == 2, "must stop at the cap, not merely notice it afterwards"


def test_tool_that_raises_does_not_crash_the_loop(case: PreflightCase, monkeypatch) -> None:
    def boom(**_):  # noqa: ANN003, ANN202
        raise RuntimeError("tool exploded")

    monkeypatch.setitem(
        __import__("capstone.tools.registry", fromlist=["_DISPATCH"])._DISPATCH,
        "inspect_file",
        boom,
    )
    agent = agent_with(
        [
            fake_response(content=[tool_use_block("inspect_file", {})]),
            fake_response(content=[tool_use_block("submit_verdict", APPROVE_PAYLOAD)]),
        ]
    )
    verdict, _ = agent.triage(case)
    # It survives, and the approval is still refused because analyse_pixels never ran.
    assert verdict.verdict is not VerdictType.APPROVE


# --------------------------------------------------------------------------------------
# finalize(): APPROVE is reachable from exactly one guarded branch
# --------------------------------------------------------------------------------------


def blocking(code: IssueCode = IssueCode.THIN_LINES) -> Issue:
    return Issue(
        code=code,
        severity=Severity.BLOCKING,
        message="too thin",
        evidence=Evidence(measured=0.2, required=0.5, unit="pt"),
    )


def test_finalize_approves_only_when_everything_is_clean() -> None:
    v = finalize(verdict=VerdictType.APPROVE, confidence=0.95, all_checks_ran=True)
    assert v.verdict is VerdictType.APPROVE


def test_finalize_downgrades_approve_carrying_issues() -> None:
    v = finalize(
        verdict=VerdictType.APPROVE, issues=[blocking()], confidence=0.99, all_checks_ran=True
    )
    assert v.verdict is VerdictType.ESCALATE
    assert v.escalation_reason is EscalationReason.SIGNALS_DISAGREE


def test_finalize_downgrades_approve_when_degraded() -> None:
    v = finalize(
        verdict=VerdictType.APPROVE, confidence=0.99, all_checks_ran=True, degraded=True
    )
    assert v.verdict is VerdictType.ESCALATE
    assert v.escalation_reason is EscalationReason.DEGRADED_MODE


def test_finalize_downgrades_approve_when_checks_did_not_all_run() -> None:
    v = finalize(verdict=VerdictType.APPROVE, confidence=0.99, all_checks_ran=False)
    assert v.verdict is VerdictType.ESCALATE
    assert v.escalation_reason is EscalationReason.JUDGEMENT_WITHOUT_CORROBORATION


def test_finalize_downgrades_approve_below_the_confidence_floor() -> None:
    v = finalize(
        verdict=VerdictType.APPROVE, confidence=CONFIDENCE_FLOOR - 0.01, all_checks_ran=True
    )
    assert v.verdict is VerdictType.ESCALATE
    assert v.escalation_reason is EscalationReason.LOW_CONFIDENCE


def test_finalize_downgrades_request_fix_with_no_blocking_issue() -> None:
    v = finalize(
        verdict=VerdictType.REQUEST_FIX,
        confidence=0.9,
        customer_message="please fix",
        issues=[],
    )
    assert v.verdict is VerdictType.ESCALATE


def test_finalize_downgrades_request_fix_with_no_message() -> None:
    v = finalize(verdict=VerdictType.REQUEST_FIX, confidence=0.9, issues=[blocking()])
    assert v.verdict is VerdictType.ESCALATE


def test_escalate_always_carries_a_reason() -> None:
    v = finalize(verdict=VerdictType.ESCALATE, confidence=0.1)
    assert v.escalation_reason is not None


# --------------------------------------------------------------------------------------
# parse_verdict(): hostile and malformed model output
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"verdict": "MAYBE", "confidence": 0.9, "issues": []},
        {"verdict": "APPROVE"},
        {"verdict": "APPROVE", "confidence": "high", "issues": []},
        {"verdict": None, "confidence": 1.0, "issues": []},
    ],
)
def test_malformed_payloads_escalate(payload: dict) -> None:
    v = parse_verdict(payload, checks=["inspect_file"], all_checks_ran=True)
    assert v.verdict is VerdictType.ESCALATE


def test_approve_with_issues_from_the_model_is_downgraded() -> None:
    v = parse_verdict(
        {
            "verdict": "APPROVE",
            "confidence": 1.0,
            "issues": [
                {
                    "code": "THIN_LINES",
                    "severity": "BLOCKING",
                    "message": "too thin",
                    "measured": 0.2,
                    "required": 0.5,
                    "unit": "pt",
                }
            ],
        },
        checks=["inspect_file", "analyse_pixels"],
        all_checks_ran=True,
    )
    assert v.verdict is VerdictType.ESCALATE


def test_issue_without_evidence_is_dropped() -> None:
    # FR-008. A finding with no measurement, region or note is vague advice, and vague
    # advice is the second-ranked failure mode. Dropping it removes the justification for
    # REQUEST_FIX, which finalize() then turns into an escalation.
    v = parse_verdict(
        {
            "verdict": "REQUEST_FIX",
            "confidence": 0.9,
            "customer_message": "please improve the quality",
            "issues": [{"code": "LOOKS_WRONG", "severity": "BLOCKING", "message": "bad"}],
        },
        checks=["inspect_file", "analyse_pixels"],
        all_checks_ran=True,
    )
    assert v.verdict is VerdictType.ESCALATE
    assert v.issues == []


def test_suspected_injection_never_approves() -> None:
    v = parse_verdict(
        {**APPROVE_PAYLOAD, "injection_suspected": True},
        checks=["inspect_file", "analyse_pixels"],
        all_checks_ran=True,
    )
    assert v.verdict is VerdictType.ESCALATE


def test_clean_approve_payload_does_approve() -> None:
    # The positive control. Without this the suite would pass by escalating everything.
    v = parse_verdict(
        APPROVE_PAYLOAD, checks=["inspect_file", "analyse_pixels"], all_checks_ran=True
    )
    assert v.verdict is VerdictType.APPROVE
