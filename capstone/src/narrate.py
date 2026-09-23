"""Narration — a small text model explains the file; code checks every claim it makes.

The model never sees pixels. It receives the scene document (`tools/scene.py`) and the
verified fix plan (`tools/fixes.py`), and writes two things: a message the customer can
act on, and a note for the reviewer. Everything it could get wrong is then checked:

- every element id (E3, T1) and fix id (F2) it mentions must exist;
- every number it writes must be one the engine produced, at the precision it wrote it.

A narration that fails the check is discarded and the deterministic template is used
instead. The model is therefore optional polish on a pipeline that already works without
it: tone, ordering, and plain language are its job; facts are not.

Two properties fall out of this design rather than being bolted on:

1. **Prompt injection through the artwork is structurally closed.** The scene carries a
   text line's position and size, never its content (there is no OCR). A file that says
   "ignore previous instructions" in 40 pt type reaches the model as
   `{"shape": "text", "height_pt": 40.0}`. limits.md S8 names injection via rendered
   text as the attack this project invites; on this path it has no carrier.
2. **Cost is fixed by layout, not resolution.** A 4,000 px upload and a 400 px one
   produce the same scene. Measured on the synthetic set: ~750 tokens median.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from capstone.src.schemas import Verdict, VerdictType
from capstone.tools.fixes import Fix, FixPlan
from capstone.tools.scene import SceneDocument

NARRATE_TOOL = "write_explanation"

# Below this redraw fidelity the scene is missing too much of the artwork for a text model
# to reason about it. Synthetic flat art sits at 0.93-0.99; a photograph-like gradient
# scores well under 0.9 (see tests).
FIDELITY_FLOOR = 0.9

SYSTEM_PROMPT = """\
You explain print-preflight results for a custom sticker and label printer.

You cannot see the artwork. You receive a measured description of it: each element's \
shape, colour, position in inches from the trim line, and distance to the safe line \
(negative means inside the keep-out margin near the cut). You also receive the rule \
findings and a list of fixes. "applied" fixes were already made to a proof image and \
re-checked; "suggested" fixes need the customer.

Rules:
- Use only facts present in the input. Every number you write must appear in the input.
- Refer to elements and fixes by their ids (E3, T1, F2) in the reviewer note.
- The customer message is plain language, no ids, no jargon beyond "bleed" and \
"safe zone". Tell them what was fixed on their proof and what they still need to do.
- If nothing needs the customer, say the proof is ready to approve.

Call the write_explanation tool with your answer. Do not reply with plain text."""

TOOL_SCHEMA: dict[str, Any] = {
    "name": NARRATE_TOOL,
    "description": "Submit the customer message and reviewer note for this file.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["customer_message", "reviewer_note", "cited_elements", "cited_fixes"],
        "properties": {
            "customer_message": {"type": "string"},
            "reviewer_note": {"type": "string"},
            "cited_elements": {"type": "array", "items": {"type": "string"}},
            "cited_fixes": {"type": "array", "items": {"type": "string"}},
        },
    },
}


class Narration(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_message: str
    reviewer_note: str
    cited_elements: tuple[str, ...] = ()
    cited_fixes: tuple[str, ...] = ()
    source: str = "template"  # "template" or the model id that wrote it


def facts(scene: SceneDocument, plan: FixPlan, verdict: Verdict) -> dict[str, Any]:
    """The whole model input. Deterministic key order so a stable prefix can cache."""
    return {
        "verdict": verdict.verdict.value,
        "scene": scene.for_model(),
        "fixes": [f.model_dump(mode="json") for f in plan.fixes],
        "remaining_after_fixes": list(plan.after),
    }


def user_message(scene: SceneDocument, plan: FixPlan, verdict: Verdict) -> str:
    return json.dumps(facts(scene, plan, verdict), sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------------------
# Claim checking
# --------------------------------------------------------------------------------------

_ID = re.compile(r"\b([ETF]\d+)\b")
_NUMBER = re.compile(r"(?<![\w#.])[-+]?\d+(?:\.\d+)?")

# Small whole numbers are allowed without a source: "both edges", "2 elements", "step 1".
# A fabricated count this small is a phrasing error, not a false measurement.
FREE_INTEGERS = frozenset(range(0, 11))


def _numbers_in(value: Any) -> list[float]:
    """Every number reachable in a JSON-like value, including ones inside strings."""
    out: list[float] = []
    if isinstance(value, bool):
        return out
    if isinstance(value, int | float):
        out.append(float(value))
    elif isinstance(value, str):
        stripped = re.sub(r"#[0-9a-fA-F]{6}", " ", _ID.sub(" ", value))
        out.extend(float(m) for m in _NUMBER.findall(stripped))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_numbers_in(v))
    elif isinstance(value, list | tuple):
        for v in value:
            out.extend(_numbers_in(v))
    return out


def _supported(written: str, allowed: list[float]) -> bool:
    """True if the number as written is some allowed value rounded to that precision.

    "0.13" is supported by 0.125; "13%" by a scale of 0.13 or 0.125; "94" by 0.94 when it
    reads as a percentage. Anything else is a number the engine did not produce.
    """
    value = float(written)
    decimals = len(written.split(".")[1]) if "." in written else 0
    if decimals == 0 and value.is_integer() and int(value) in FREE_INTEGERS:
        return True
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    for a in allowed:
        if abs(abs(value) - abs(a)) <= tolerance:
            return True
        if abs(abs(value) - abs(a) * 100) <= tolerance:  # written as a percentage
            return True
    return False


def check_claims(narration: Narration, scene: SceneDocument, plan: FixPlan) -> list[str]:
    """Everything in the narration the engine cannot vouch for. Empty means it passed."""
    known_elements = {e.id for e in scene.elements}
    known_fixes = {f.id for f in plan.fixes}
    problems: list[str] = []

    text = f"{narration.customer_message}\n{narration.reviewer_note}"
    for ident in (
        set(_ID.findall(text)) | set(narration.cited_elements) | set(narration.cited_fixes)
    ):
        pool = known_fixes if ident.startswith("F") else known_elements
        if ident not in pool:
            problems.append(f"unknown id {ident}")

    allowed = _numbers_in(scene.for_model()) + _numbers_in(
        [f.model_dump(mode="json") for f in plan.fixes]
    )
    cleaned = re.sub(r"#[0-9a-fA-F]{6}", " ", _ID.sub(" ", text))
    for written in _NUMBER.findall(cleaned):
        if not _supported(written, allowed):
            problems.append(f"unsupported number {written}")
    return problems


# --------------------------------------------------------------------------------------
# The two narrators
# --------------------------------------------------------------------------------------


def template_narration(scene: SceneDocument, plan: FixPlan, verdict: Verdict) -> Narration:
    """No model. Built only from the fixes' own sentences, so it passes the check by
    construction — and it is what ships whenever a model narration does not."""
    applied = [f for f in plan.fixes if f.kind == "applied" and f.verified]
    todo = [f for f in plan.fixes if f.kind == "suggested"]
    # An applied fix the re-check did not clear is neither done nor the customer's job:
    # it goes to a reviewer, and the customer is told only that someone will look.
    unverified = [f for f in plan.fixes if f.kind == "applied" and not f.verified]

    lines: list[str] = []
    if applied:
        lines.append("We corrected your proof:")
        lines += _grouped(applied)
    if todo:
        lines.append("Before we can print, please update your file:")
        lines += _grouped(todo)
    if unverified:
        lines.append("A member of our team will check the rest before printing.")
    elif not todo:
        lines.append("Your proof is ready to approve.")
    if not plan.fixes:
        lines = ["Your file passed every check. It is ready to print."]

    def status(f: Fix) -> str:
        if f.verified is None:
            return f.kind
        return f"{f.kind} {'verified' if f.verified else 'NOT verified'}"

    note = [f"verdict {verdict.verdict.value}; scene fidelity {scene.fidelity}"]
    note += [f"{f.id} {status(f)}: {f.action}" for f in plan.fixes]
    return Narration(
        customer_message="\n".join(lines),
        reviewer_note="\n".join(note),
        cited_elements=tuple(sorted({f.target for f in plan.fixes if f.target})),
        cited_fixes=tuple(f.id for f in plan.fixes),
    )


def _grouped(fixes: list[Fix]) -> list[str]:
    """One customer line per distinct action, with a count instead of repeats."""
    counts: dict[str, int] = {}
    for f in fixes:
        line = _strip_ids(f.action)
        counts[line] = counts.get(line, 0) + 1
    return [f"- {line}" + (f" ({n} places)" if n > 1 else "") for line, n in counts.items()]


def _strip_ids(text: str) -> str:
    """Customers do not know what E4 is."""
    return re.sub(r"\s*\b[ETF]\d+\b", " one element", text, count=1).replace("  ", " ")


def narrate_with_model(
    client: Any, model: str, scene: SceneDocument, plan: FixPlan, verdict: Verdict
) -> tuple[Narration, list[str]]:
    """Ask a text model to narrate; fall back to the template if its claims do not check.

    Returns the narration that should ship and the problems found in the model's attempt
    (empty if it passed). Never raises for model misbehaviour: a bad narration is a
    fallback, not an outage.
    """
    fallback = template_narration(scene, plan, verdict)
    if verdict.verdict is VerdictType.APPROVE and not plan.fixes:
        return fallback, []  # nothing to explain; no call
    if scene.fidelity is not None and scene.fidelity < FIDELITY_FLOOR:
        # The description cannot reproduce the artwork, so a model reading it would be
        # reasoning about a different image. No call; the template ships.
        return fallback, [f"scene fidelity {scene.fidelity} below {FIDELITY_FLOOR}; no call"]

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": user_message(scene, plan, verdict)}],
    )
    block = next(
        (
            b
            for b in response.content
            if getattr(b, "type", "") == "tool_use" and b.name == NARRATE_TOOL
        ),
        None,
    )
    if block is None:
        return fallback, [f"no {NARRATE_TOOL} call (stop_reason={response.stop_reason})"]
    try:
        narration = Narration.model_validate({**dict(block.input), "source": model})
    except ValidationError as exc:
        return fallback, [f"schema: {exc.error_count()} error(s)"]
    problems = check_claims(narration, scene, plan)
    return (fallback, problems) if problems else (narration, [])


__all__ = [
    "FIDELITY_FLOOR",
    "NARRATE_TOOL",
    "Narration",
    "SYSTEM_PROMPT",
    "check_claims",
    "facts",
    "narrate_with_model",
    "template_narration",
    "user_message",
]
