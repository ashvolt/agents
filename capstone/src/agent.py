"""The preflight agent: a tool loop around one HTTP call, plus the verdict chokepoint.

Two structural commitments, both from the constitution:

**`finalize()` is the only way a Verdict leaves this module.** Success, exception, budget
exhaustion, schema failure, refusal, truncation — every path returns through it, and
APPROVE is reachable from exactly one guarded branch. Principle IV as structure rather
than as intention; `test_agent_failure_paths.py` injects each failure and asserts none
produces an approval.

**`stop_reason` is checked before `response.content` is read.** A truncated generation is
not a bad verdict, it is not a verdict at all, and parsing it is how a half-written
`{"verdict": "APPROVE"...` becomes a bad print.
"""

from __future__ import annotations

import base64
import io
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic
from PIL import Image
from pydantic import ValidationError

from capstone.ops.budgets import Budget, BudgetExhausted
from capstone.ops.pricing import UnknownModelError, estimate_cost
from capstone.ops.tracing import attach_trace
from capstone.src.product_specs import UnknownProductError, get_spec
from capstone.src.prompts import (
    NO_TOOLS_SUFFIX,
    SYSTEM_PROMPT,
    UNTRUSTED_CONTENT_FRAMING,
    order_context,
)
from capstone.src.schemas import (
    EscalationReason,
    Evidence,
    Issue,
    IssueCode,
    PreflightCase,
    Severity,
    Trace,
    TraceStep,
    Verdict,
    VerdictType,
)
from capstone.tools.registry import (
    SUBMIT_VERDICT,
    dispatch,
    measurement_tools,
    verdict_tool,
)

# Imported for its side effect: loading .env before any client is constructed. The SDK
# resolves credentials at construction time, so a later import is too late - which is
# exactly the bug this line fixes.
from shared.env import cheap_model  # noqa: E402

# Confidence below this escalates regardless of what the model concluded. OQ-2: fitted on
# the train split in Phase 5, not guessed. This is the placeholder the tuning starts from.
CONFIDENCE_FLOOR = 0.70

# Artwork is downscaled before it reaches the model. At ~1100px the long edge an image
# costs roughly 1,600 tokens; full resolution costs many times that and buys nothing for
# a judgement about where content sits relative to a margin.
MAX_IMAGE_EDGE_PX = 1100

MAX_OUTPUT_TOKENS = 2048
REPAIR_ATTEMPTS = 1


def _model_id() -> str:
    """Model from env, never hardcoded (constitution, Engineering Constraints).

    CAPSTONE_MODEL overrides, so a sweep can be pointed at Opus for a graded run without
    editing anything.
    """
    return os.environ.get("CAPSTONE_MODEL") or cheap_model()


# --------------------------------------------------------------------------------------
# The chokepoint
# --------------------------------------------------------------------------------------


def finalize(
    *,
    verdict: VerdictType,
    issues: list[Issue] | None = None,
    confidence: float,
    customer_message: str | None = None,
    escalation_reason: EscalationReason | None = None,
    checks_completed: list[str] | None = None,
    degraded: bool = False,
    all_checks_ran: bool = False,
) -> Verdict:
    """The single exit. Every Verdict this module produces comes through here.

    APPROVE survives only when all of the following hold: it was asked for, no issues
    were found, every check ran, nothing was degraded, and confidence clears the floor.
    Any failure downgrades to ESCALATE with a reason rather than raising, because raising
    would push the decision back into a caller's `except` block — which is where default
    approvals come from.
    """
    issues = list(issues or [])
    checks = list(checks_completed or [])

    if verdict is VerdictType.APPROVE:
        reason: EscalationReason | None = None
        if issues:
            reason = EscalationReason.SIGNALS_DISAGREE
        elif degraded:
            reason = EscalationReason.DEGRADED_MODE
        elif not all_checks_ran:
            reason = EscalationReason.JUDGEMENT_WITHOUT_CORROBORATION
        elif confidence < CONFIDENCE_FLOOR:
            reason = EscalationReason.LOW_CONFIDENCE

        if reason is not None:
            return Verdict(
                verdict=VerdictType.ESCALATE,
                issues=issues,
                confidence=confidence,
                escalation_reason=reason,
                checks_completed=checks,
                degraded=degraded,
            )
        return Verdict(
            verdict=VerdictType.APPROVE,
            issues=[],
            confidence=confidence,
            checks_completed=checks,
            degraded=False,
        )

    if verdict is VerdictType.REQUEST_FIX:
        blocking = [i for i in issues if i.severity is Severity.BLOCKING]
        if blocking and (customer_message or "").strip():
            return Verdict(
                verdict=VerdictType.REQUEST_FIX,
                issues=issues,
                confidence=confidence,
                customer_message=customer_message,
                checks_completed=checks,
                degraded=degraded,
            )
        # Asked for a fix but could not justify one: that is a judgement call, not a fix.
        return Verdict(
            verdict=VerdictType.ESCALATE,
            issues=issues,
            confidence=confidence,
            escalation_reason=EscalationReason.JUDGEMENT_WITHOUT_CORROBORATION,
            checks_completed=checks,
            degraded=degraded,
        )

    return Verdict(
        verdict=VerdictType.ESCALATE,
        issues=issues,
        confidence=confidence,
        escalation_reason=escalation_reason or EscalationReason.LOW_CONFIDENCE,
        checks_completed=checks,
        degraded=degraded,
    )


def escalate(
    reason: EscalationReason,
    *,
    issues: list[Issue] | None = None,
    checks: list[str] | None = None,
    confidence: float = 0.0,
    degraded: bool = True,
) -> Verdict:
    return finalize(
        verdict=VerdictType.ESCALATE,
        issues=issues,
        confidence=confidence,
        escalation_reason=reason,
        checks_completed=checks,
        degraded=degraded,
    )


# --------------------------------------------------------------------------------------
# Parsing the model's verdict
# --------------------------------------------------------------------------------------


def _issue_from_payload(raw: dict[str, Any]) -> Issue | None:
    """Build an Issue, or None when the model produced something unusable.

    Evidence is required (FR-008). A finding with no measurement, region, or note is
    dropped rather than passed on as vague advice — and dropping it means the verdict
    loses its justification, which `finalize` then turns into an escalation.
    """
    try:
        code = IssueCode(raw["code"])
        severity = Severity(raw.get("severity", "BLOCKING"))
        message = str(raw["message"]).strip()
        if not message:
            return None
        region = raw.get("region")
        evidence = Evidence(
            measured=raw.get("measured"),
            required=raw.get("required"),
            unit=raw.get("unit"),
            region=tuple(region) if region else None,  # type: ignore[arg-type]
            note=raw.get("note"),
        )
    except (KeyError, ValueError, ValidationError):
        return None
    return Issue(code=code, severity=severity, message=message, evidence=evidence)


def parse_verdict(payload: dict[str, Any], checks: list[str], all_checks_ran: bool) -> Verdict:
    """Turn `submit_verdict` input into a validated Verdict via `finalize`."""
    try:
        verdict_type = VerdictType(payload["verdict"])
        confidence = float(payload.get("confidence", 0.0))
    except (KeyError, ValueError, TypeError):
        return escalate(EscalationReason.SCHEMA_INVALID, checks=checks)

    issues: list[Issue] = []
    for raw in payload.get("issues") or []:
        if isinstance(raw, dict):
            issue = _issue_from_payload(raw)
            if issue is not None:
                issues.append(issue)

    reason_raw = payload.get("escalation_reason")
    reason: EscalationReason | None = None
    if reason_raw:
        try:
            reason = EscalationReason(reason_raw)
        except ValueError:
            reason = EscalationReason.SCHEMA_INVALID

    if payload.get("injection_suspected"):
        # Principle V: a file that tried to instruct the agent never auto-approves.
        return escalate(
            EscalationReason.SIGNALS_DISAGREE,
            issues=issues,
            checks=checks,
            confidence=confidence,
            degraded=False,
        )

    return finalize(
        verdict=verdict_type,
        issues=issues,
        confidence=confidence,
        customer_message=payload.get("customer_message"),
        escalation_reason=reason,
        checks_completed=checks,
        all_checks_ran=all_checks_ran,
    )


# --------------------------------------------------------------------------------------
# Image
# --------------------------------------------------------------------------------------


def encode_image(path: Path) -> tuple[str, str] | None:
    """Downscaled PNG as (base64, media_type), or None if the file will not decode."""
    try:
        with Image.open(path) as img:
            img.load()
            img = img.convert("RGB")
            img.thumbnail((MAX_IMAGE_EDGE_PX, MAX_IMAGE_EDGE_PX))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
    except (OSError, ValueError):
        return None
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/png"


# --------------------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------------------


class PreflightAgent:
    """One agent, reused across a sweep so the client and the cached prefix persist."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        *,
        model: str | None = None,
        use_tools: bool = True,
    ) -> None:
        self.client = client or anthropic.Anthropic()
        self.model = model or _model_id()
        self.use_tools = use_tools
        self._system = SYSTEM_PROMPT if use_tools else SYSTEM_PROMPT + NO_TOOLS_SUFFIX

    # -- request assembly ---------------------------------------------------------------

    def _tools(self) -> list[dict[str, Any]]:
        tools = [verdict_tool()]
        if self.use_tools:
            tools = measurement_tools() + tools
        return tools

    def _system_blocks(self) -> list[dict[str, Any]]:
        # Frozen text plus a cache breakpoint: tools and system form the stable prefix,
        # and per-case content goes after it. research.md D-7.
        return [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}]

    def _initial_messages(self, case: PreflightCase, spec_name: str) -> list[dict[str, Any]] | None:
        encoded = encode_image(case.image_path)
        if encoded is None:
            return None
        data, media_type = encoded
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": UNTRUSTED_CONTENT_FRAMING},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    },
                    {
                        "type": "text",
                        "text": order_context(
                            case.order.product_id,
                            spec_name,
                            case.order.width_in,
                            case.order.height_in,
                            case.order.quantity,
                        ),
                    },
                ],
            }
        ]

    # -- the run ------------------------------------------------------------------------

    def triage(self, case: PreflightCase) -> tuple[Verdict, Trace]:
        """Check one file. Never raises."""
        trace = Trace(
            case_id=case.case_id,
            order_id=case.order.order_id,
            started_at=datetime.now(UTC),
            model=self.model,
        )
        budget = Budget()
        checks: list[str] = []

        try:
            spec = get_spec(case.order.product_id)
        except UnknownProductError:
            trace.termination = "unsupported_product"
            return escalate(EscalationReason.UNSUPPORTED_INPUT, checks=checks), trace

        messages = self._initial_messages(case, spec.display_name)
        if messages is None:
            trace.termination = "unreadable_file"
            issue = Issue(
                code=IssueCode.UNREADABLE_FILE,
                severity=Severity.BLOCKING,
                message="The uploaded file could not be opened.",
                evidence=Evidence(note=f"could not decode {case.image_path.name}"),
            )
            return escalate(
                EscalationReason.UNSUPPORTED_INPUT, issues=[issue], checks=checks
            ), trace

        tools = self._tools()
        repair_used = 0

        while True:
            try:
                budget.check()
            except BudgetExhausted as exc:
                trace.termination = f"budget_{exc.which}"
                trace.steps.append(
                    TraceStep(
                        index=len(trace.steps), kind="validation", name="budget",
                        duration_ms=0, ok=False, detail=budget.summary(),
                    )
                )
                return escalate(
                    EscalationReason.BUDGET_EXHAUSTED, checks=checks, degraded=True
                ), trace

            t0 = time.perf_counter()
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    system=self._system_blocks(),
                    messages=messages,
                    tools=tools,
                )
            except anthropic.APIError as exc:
                trace.termination = "model_error"
                trace.steps.append(
                    TraceStep(
                        index=len(trace.steps), kind="model_turn", name="api_error",
                        duration_ms=int((time.perf_counter() - t0) * 1000), ok=False,
                        detail={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )
                # Principle: degrade, do not guess. Deterministic findings still stand.
                return escalate(EscalationReason.MODEL_ERROR, checks=checks), trace

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._account(response, trace, budget)
            trace.steps.append(
                TraceStep(
                    index=len(trace.steps), kind="model_turn", name="messages.create",
                    duration_ms=elapsed_ms, ok=True,
                    detail={"stop_reason": response.stop_reason},
                )
            )

            # stop_reason BEFORE content. A truncated response is not a verdict.
            stop = response.stop_reason
            if stop == "refusal":
                trace.termination = "refusal"
                return escalate(EscalationReason.REFUSAL, checks=checks), trace
            if stop == "max_tokens":
                trace.termination = "truncated"
                return escalate(EscalationReason.TRUNCATED_RESPONSE, checks=checks), trace

            if stop != "tool_use":
                # Answered in prose instead of submitting. One repair attempt, then stop.
                if repair_used < REPAIR_ATTEMPTS:
                    repair_used += 1
                    messages = messages + [
                        {"role": "assistant", "content": response.content},
                        {
                            "role": "user",
                            "content": (
                                "You did not submit a verdict. Call the submit_verdict "
                                "tool now with your decision."
                            ),
                        },
                    ]
                    continue
                trace.termination = "no_verdict"
                return escalate(EscalationReason.SCHEMA_INVALID, checks=checks), trace

            # Execute tool calls; a submit_verdict block ends the run.
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                if block.name == SUBMIT_VERDICT:
                    all_ran = (not self.use_tools) or {
                        "inspect_file",
                        "analyse_pixels",
                    } <= set(checks)
                    verdict = parse_verdict(dict(block.input), checks, all_ran)
                    trace.termination = "completed"
                    trace.injection_suspected = bool(
                        dict(block.input).get("injection_suspected")
                    )
                    return verdict, trace

                t1 = time.perf_counter()
                result = dispatch(
                    block.name,
                    dict(block.input),
                    image_path=case.image_path,
                    order=case.order,
                )
                if block.name not in checks:
                    checks.append(block.name)
                trace.steps.append(
                    TraceStep(
                        index=len(trace.steps), kind="tool_call", name=block.name,
                        duration_ms=int((time.perf_counter() - t1) * 1000), ok=True,
                        detail={"bytes": len(result)},
                    )
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

            if not tool_results:
                trace.termination = "no_progress"
                return escalate(EscalationReason.SCHEMA_INVALID, checks=checks), trace

            # All tool results go back in ONE user message. Splitting them teaches the
            # model to stop making parallel calls.
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]

    def _account(self, response: Any, trace: Trace, budget: Budget) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            budget.charge()
            return
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        trace.input_tokens += inp
        trace.output_tokens += out
        trace.cache_read_tokens += cache_read
        trace.cache_write_tokens += cache_write

        # Price against the model the API says served the request, not the one requested.
        served = getattr(response, "model", None) or self.model
        try:
            cost = estimate_cost(
                served, inp, out,
                cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            )
        except UnknownModelError:
            # Fall back to the model we *asked* for rather than to zero. A zero here is
            # the lying-dashboard failure ops/pricing.py exists to prevent, and it also
            # disables the sweep spend cap, which reads this number.
            try:
                cost = estimate_cost(
                    self.model, inp, out,
                    cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                )
                note = "priced against requested model"
            except UnknownModelError:
                cost = 0.0
                note = "UNPRICED - cost accounting and spend cap are both blind"
            trace.steps.append(
                TraceStep(
                    index=len(trace.steps), kind="validation", name="unpriced_model",
                    duration_ms=0, ok=False,
                    detail={"served": served, "requested": self.model, "note": note},
                )
            )
        trace.cost_usd += cost
        budget.charge(tokens=inp + out + cache_read, cost_usd=cost)


# --------------------------------------------------------------------------------------
# Harness adapter
# --------------------------------------------------------------------------------------


def make_triage_fn(
    *, use_tools: bool = True, model: str | None = None
) -> Any:
    """A `Callable[[PreflightCase], Verdict]` for the eval harness.

    One agent instance for the whole sweep, so the HTTP client and the cached prefix are
    reused — a fresh client per case would forfeit both.
    """
    agent = PreflightAgent(model=model, use_tools=use_tools)

    def triage(case: PreflightCase) -> Verdict:
        verdict, trace = agent.triage(case)
        attach_trace(trace)
        return verdict

    return triage


__all__ = [
    "CONFIDENCE_FLOOR",
    "PreflightAgent",
    "encode_image",
    "escalate",
    "finalize",
    "make_triage_fn",
    "parse_verdict",
]
