"""The eval harness.

Runs any `Callable[[PreflightCase], Verdict]` against the labelled set and scores it.
Built before the agent, per Constitution Principle II: a baseline has to exist before
anything can claim to improve on it.

Two rules the harness enforces rather than trusts:

1. **The holdout split is not readable by default.** `--split holdout` exists, but
   `load_cases()` returns train only unless asked, so a tuning loop cannot reach it by
   forgetting a flag.
2. **A crashing callable does not crash the sweep.** An exception becomes an ESCALATE
   verdict with `termination="error"` and is counted by SC-007. A harness that dies on
   the first bad case cannot measure reliability.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from capstone.evals.metrics import SweepReport, score
from capstone.ops.tracing import take_trace
from capstone.src.schemas import (
    EscalationReason,
    GoldLabel,
    OrderMetadata,
    PreflightCase,
    RunResult,
    Split,
    Trace,
    TraceStep,
    Verdict,
    VerdictType,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "capstone" / "data" / "cases.jsonl"
RUNS_DIR = REPO_ROOT / "capstone" / "evals" / "runs"

TriageFn = Callable[[PreflightCase], Verdict]


def load_cases(
    manifest: Path | None = None,
    split: Split | None = Split.TRAIN,
) -> list[tuple[PreflightCase, GoldLabel]]:
    """Load cases and labels.

    `split=Split.TRAIN` is the default on purpose. Reading the holdout requires saying so
    explicitly, because the number it produces is only meaningful once.
    """
    manifest = manifest or DEFAULT_MANIFEST
    if not manifest.exists():
        raise FileNotFoundError(
            f"no dataset at {manifest}. Run: python -m capstone.data.generate -n 200"
        )

    out: list[tuple[PreflightCase, GoldLabel]] = []
    with manifest.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            label = GoldLabel.model_validate(row["label"])
            if split is not None and label.split is not split:
                continue
            case = PreflightCase(
                case_id=row["case_id"],
                image_path=REPO_ROOT / row["image_path"],
                order=OrderMetadata.model_validate(row["order"]),
            )
            out.append((case, label))
    return out


def _error_verdict(exc: BaseException) -> Verdict:
    """Constitution Principle IV: a crash is an escalation, never an approval."""
    return Verdict(
        verdict=VerdictType.ESCALATE,
        confidence=0.0,
        escalation_reason=EscalationReason.INTERNAL_ERROR,
        degraded=True,
        checks_completed=[],
    )


def run_one(triage: TriageFn, case: PreflightCase, label: GoldLabel) -> RunResult:
    """Run one case. Never raises."""
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    trace: Trace | None = None
    try:
        verdict = triage(case)
        termination = "completed"
        detail: dict = {}
    except Exception as exc:  # noqa: BLE001 - the harness must survive any callable
        verdict = _error_verdict(exc)
        termination = "error"
        detail = {"exception": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # A triage function may attach its own trace via `attach_trace`; otherwise synthesise
    # a minimal one so cost and latency are still accounted for.
    attached = take_trace(case.case_id)
    if attached is None:
        trace = Trace(
            case_id=case.case_id,
            order_id=case.order.order_id,
            started_at=started,
            ended_at=datetime.now(UTC),
            latency_ms=latency_ms,
            verdict=verdict.verdict,
            termination=termination,
        )
    else:
        trace = attached
        trace.latency_ms = latency_ms
        trace.verdict = verdict.verdict
        trace.ended_at = datetime.now(UTC)
        if termination == "error":
            trace.termination = "error"

    if detail:
        trace.steps.append(
            TraceStep(
                index=len(trace.steps),
                kind="validation",
                name="harness_error",
                duration_ms=latency_ms,
                ok=False,
                detail=detail,
            )
        )

    return RunResult(case_id=case.case_id, verdict=verdict, label=label, trace=trace)


class SpendLimitReached(RuntimeError):
    """Raised when a sweep's cumulative cost passes the cap it was given."""


def sweep(
    triage: TriageFn,
    cases: Iterable[tuple[PreflightCase, GoldLabel]] | None = None,
    *,
    arm: str = "default",
    split: Split | None = Split.TRAIN,
    limit: int | None = None,
    progress: bool = False,
    max_spend_usd: float | None = None,
) -> tuple[SweepReport, list[RunResult]]:
    """Run the set and score it.

    `max_spend_usd` is a hard stop on cumulative cost. It is checked *between* cases, so
    the worst overshoot is one case — and the partial report is still returned and scored
    rather than thrown away, because a truncated measurement beats no measurement.

    This exists because the per-file budget in ops/budgets.py caps one run and cannot see
    the sweep. A loop that costs 3x expectation stays inside its per-file budget on every
    single case and still empties an account across 159 of them.
    """
    pairs = list(cases) if cases is not None else load_cases(split=split)
    if limit:
        pairs = pairs[:limit]

    results: list[RunResult] = []
    spent = 0.0
    stopped_early = False

    for i, (case, label) in enumerate(pairs, 1):
        result = run_one(triage, case, label)
        results.append(result)
        spent += result.trace.cost_usd

        if progress and i % 25 == 0:
            print(f"  ... {i}/{len(pairs)}   spent ${spent:.3f}")

        if max_spend_usd is not None and spent >= max_spend_usd:
            stopped_early = True
            print(
                f"\n*** SPEND CAP HIT: ${spent:.3f} of ${max_spend_usd:.2f} after "
                f"{i}/{len(pairs)} cases. Stopping. ***"
            )
            print("    The partial report below covers only the cases that ran.")
            break

    report = score(results, arm=arm + ("--partial" if stopped_early else ""))
    return report, results


def write_run(report: SweepReport, results: list[RunResult], out_dir: Path | None = None) -> Path:
    """Persist a run so the report is reproducible from the log rather than from memory."""
    out_dir = out_dir or RUNS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}-{report.arm}.json"
    path.write_text(
        json.dumps(
            {
                "report": report.to_dict(),
                "results": [
                    {
                        "case_id": r.case_id,
                        "verdict": r.verdict.model_dump(mode="json"),
                        "label": r.label.model_dump(mode="json"),
                        "false_approve": r.is_false_approve,
                        "false_reject": r.is_false_reject,
                        "termination": r.trace.termination,
                        "cost_usd": r.trace.cost_usd,
                        "latency_ms": r.trace.latency_ms,
                    }
                    for r in results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _resolve_arm(name: str, no_tools: bool) -> tuple[str, TriageFn]:
    from capstone.evals import baselines

    if name == "always_escalate":
        return "always_escalate", baselines.always_escalate
    if name == "always_approve":
        return "always_approve", baselines.always_approve
    if name == "rules_only":
        return "rules_only", baselines.rules_only
    if name == "cv_decider":
        from capstone.src.deciders import make_cv_decider_triage

        return "cv_decider", make_cv_decider_triage()
    if name in ("agent", "agent_fast"):
        from capstone.src.agent import make_triage_fn

        precomputed = name == "agent_fast"
        label = "agent--no-tools" if no_tools else ("agent_fast" if precomputed else "agent")
        return label, make_triage_fn(use_tools=not no_tools, precomputed=precomputed)
    raise SystemExit(f"unknown arm {name!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the preflight eval set.")
    ap.add_argument(
        "arm",
        nargs="?",
        default="rules_only",
        choices=[
            "always_escalate", "always_approve", "rules_only", "cv_decider", "agent", "agent_fast"
        ],
    )
    ap.add_argument(
        "--no-tools",
        action="store_true",
        help="control arm: disable deterministic tools so the model works unaided",
    )
    ap.add_argument(
        "--split",
        default="train",
        choices=["train", "holdout", "all"],
        help="holdout is scored ONCE, at the end of the project (T110)",
    )
    ap.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    ap.add_argument(
        "--max-spend",
        type=float,
        default=2.50,
        help=(
            "hard stop once cumulative cost passes this many dollars (default 2.50). "
            "A full 159-case Haiku sweep is expected to cost ~1.20-2.30, so this stops a "
            "runaway without truncating a legitimate worst case. Pass 0 to disable."
        ),
    )
    ap.add_argument("--save", action="store_true", help="write the run to capstone/evals/runs/")
    ap.add_argument(
        "--manifest", type=str, default=None, help="dataset manifest (default cases.jsonl)"
    )
    args = ap.parse_args()

    split = None if args.split == "all" else Split(args.split)
    if split is Split.HOLDOUT:
        print("*** Scoring the HOLDOUT split. This number is only meaningful once. ***\n")

    arm_name, fn = _resolve_arm(args.arm, args.no_tools)
    cap = args.max_spend if args.max_spend and args.max_spend > 0 else None
    if cap is not None:
        print(f"spend cap: ${cap:.2f}\n")
    pairs = load_cases(
        manifest=Path(args.manifest) if args.manifest else None, split=split
    )
    report, results = sweep(
        fn, pairs, arm=arm_name, limit=args.limit, progress=True, max_spend_usd=cap
    )
    print(report.render())

    if args.save:
        path = write_run(report, results)
        print(f"\nwrote {path}")

    raise SystemExit(0 if report.passes else 1)


if __name__ == "__main__":
    main()


__all__ = ["TriageFn", "load_cases", "run_one", "sweep", "write_run"]
