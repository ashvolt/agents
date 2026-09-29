"""The customer's half of a verdict, rendered in one place.

Two audiences read a finding and they want opposite things. The production artist wants
`137.9`, a region, and which branch of `effective_dpi` fired. The customer wants to know
what will happen to their sticker and what to do about it. Before this module there was
one string for both - `Evidence.describe` says so in its own docstring, "for the
escalation queue and customer message" - and `_customer_message` fed `Issue.message`
straight to the customer as a bullet. One field tuned for two readers is tuned for
neither.

So: `Issue.message` stays the artist's line and never changes. `Issue.advice`
(`schemas.CustomerAdvice`) carries the customer register, and this module is the only
thing that turns it into prose.

**Layering, not dumbing down.** Each finding renders as consequence, then action, then
the measurement last and labelled. The technical detail is kept - a designer reading the
letter can still act on `137.9 dpi` - but it no longer leads, and it is no longer the
only thing present. The result is *denser* than the single sentence it replaces, not
thinner.

**Why `avoid` exists.** See `CustomerAdvice`. A message phrased as a threshold invites
the customer to satisfy the threshold, and for half these codes the easy way to do that
leaves the artwork just as broken while passing the re-check. Naming the wrong fix is the
only part of the letter that protects the press.

One renderer, two callers (`deciders.measure` via `evals.baselines`, and
`agent._fallback_customer_message`). They used to word the same letter differently, which
meant a customer could get either depending on which arm ran.
"""

from __future__ import annotations

from capstone.src.schemas import Issue, Severity

# Slot labels. Fixed here so every surface - the demo, the MCP server, the review queue -
# shows a customer the same shape, and so a reviewer scanning the queue can see at a
# glance which findings carry an action and which do not.
ACTION_LABEL = "What to do"
AVOID_LABEL = "Please avoid"
TECHNICAL_LABEL = "Technical detail"

OPENING_WITH_ORDER = (
    "We reviewed the artwork for order {order_id} and found {n} item{s} to fix "
    "before we print:"
)
OPENING_WITHOUT_ORDER = "We found {n} item{s} to fix before we print your artwork:"
CLOSING = "Reply with an updated file and we'll re-check it right away."


def issue_lines(issue: Issue, number: int | None = None) -> list[str]:
    """One finding as the customer sees it. Lines are unwrapped on purpose.

    Wrapping is the surface's job: this text lands in an HTML `<pre>`, a Streamlit
    `st.info`, and a SQLite column, and each wants a different width. Hard-wrapping here
    would look wrong in two of the three.

    A finding with no `advice` falls back to the artist's line, which is what the old
    letter did for every finding. That is the right fallback for a model-authored issue
    (the model writes one string) and it is why this function never invents an action it
    was not given.
    """
    lead = f"{number}. " if number is not None else "- "
    pad = " " * len(lead)
    detail = issue.evidence.describe()

    if issue.advice is None:
        lines = [f"{lead}{issue.message}"]
        if detail:
            lines.append(f"{pad}({detail})")
        return lines

    lines = [f"{lead}{issue.advice.headline}", f"{pad}{ACTION_LABEL}: {issue.advice.action}"]
    if issue.advice.avoid:
        lines.append(f"{pad}{AVOID_LABEL}: {issue.advice.avoid}")
    if detail:
        lines.append(f"{pad}{TECHNICAL_LABEL}: {detail}.")
    return lines


def customer_letter(issues: list[Issue], *, order_id: str | None = None) -> str:
    """The message an artist approves before it is sent.

    `issues` is the blocking set: advisories are a reason to route the file to a person,
    not something to ask the customer about. Findings are numbered so the closing line
    ("an updated file") clearly answers all of them at once.
    """
    findings = [i for i in issues if i.severity is Severity.BLOCKING] or list(issues)
    plural = "s" if len(findings) != 1 else ""
    opening = (
        OPENING_WITH_ORDER.format(order_id=order_id, n=len(findings), s=plural)
        if order_id
        else OPENING_WITHOUT_ORDER.format(n=len(findings), s=plural)
    )

    lines = [opening, ""]
    for index, issue in enumerate(findings, start=1):
        lines += issue_lines(issue, index)
        lines.append("")
    lines.append(CLOSING)
    return "\n".join(lines)


__all__ = [
    "ACTION_LABEL",
    "AVOID_LABEL",
    "TECHNICAL_LABEL",
    "customer_letter",
    "issue_lines",
]
