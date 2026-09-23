"""Trace collection.

This module exists to be **imported the same way from everywhere**, which is not a
stylistic preference — it is the fix for a real bug.

The sink originally lived in `capstone/evals/harness.py`. Running the harness as
`python -m capstone.evals.harness` executes that file as `__main__`, and the agent then
does `from capstone.evals.harness import attach_trace`, which imports the *same file
again* under its package name. Python now holds two module objects, each with its own
`TRACE_SINK` dict: the agent writes to one, the harness reads from the other, and every
trace arrives empty.

The symptom was `cost/file $0.0000` across a whole sweep — identical to the symptom of
the unpriced-model bug fixed at the same time, from a completely unrelated cause. Both
failed silently and in the safe-looking direction, which is why cost accounting gets its
own tests.

Nothing imports this as `__main__`, so there is exactly one sink.
"""

from __future__ import annotations

import json
from pathlib import Path

from capstone.src.schemas import Trace

# case_id -> Trace, handed from whatever produced it to whatever is scoring it.
#
# Traces are deliberately kept off the Verdict: the Verdict is the output contract the
# spec defines, and smuggling observability fields into it would mean the thing the model
# is asked to produce and the thing the system records are no longer the same shape.
TRACE_SINK: dict[str, Trace] = {}


def attach_trace(trace: Trace) -> None:
    """Hand a trace to whoever is running this case."""
    TRACE_SINK[trace.case_id] = trace


def take_trace(case_id: str) -> Trace | None:
    """Claim the trace for a case, if one was attached. Removes it from the sink."""
    return TRACE_SINK.pop(case_id, None)


def clear() -> None:
    """Drop anything left behind. A leaked trace means a case that never got scored."""
    TRACE_SINK.clear()


def write_jsonl(traces: list[Trace], path: Path) -> Path:
    """Append traces to a JSONL file, one object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for trace in traces:
            fh.write(json.dumps(trace.model_dump(mode="json")) + "\n")
    return path


__all__ = ["TRACE_SINK", "attach_trace", "clear", "take_trace", "write_jsonl"]
