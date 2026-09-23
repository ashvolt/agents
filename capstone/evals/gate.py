"""The CI regression gate.

PLAN.md's L5 exit test is "a red CI build caused by an agent quality regression, not a
code bug." This is the thing that makes that possible.

It runs the deterministic arm — no API key, no spend, no network — against the committed
baseline in `capstone/evals/baseline.json` and fails the build when quality moves the
wrong way. The agent arm costs money and is not run in CI; the deterministic pipeline is
the system of record (results.md §7), so gating on it protects the thing that actually
ships.

Three ways to fail, in order of seriousness:

1. **SC-002 breached.** A false-approve rate above the limit fails outright, whatever the
   approve rate says. It is a constraint, not a metric to trade.
2. **Approve rate regressed** beyond tolerance. Quality went backwards.
3. **Per-issue recall regressed** on any single code beyond tolerance, even if the
   headline numbers held. This catches a change that trades one defect class for another
   — which the aggregate would hide.

Usage:
    python -m capstone.evals.gate                 # check against the baseline
    python -m capstone.evals.gate --update        # record a new baseline (deliberate)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capstone.evals.baselines import rules_only
from capstone.evals.harness import load_cases, sweep
from capstone.evals.metrics import FALSE_APPROVE_LIMIT, SweepReport
from capstone.src.schemas import Split

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "capstone" / "evals" / "baseline.json"
MANIFEST = REPO_ROOT / "capstone" / "data" / "cases_large.jsonl"

# How far a number may drift before the build goes red. Not zero: the deterministic arm
# is bit-identical run to run (SC-008), so any movement here is a real change in
# behaviour rather than noise — but a small allowance keeps a one-case flip from blocking
# an unrelated refactor.
APPROVE_RATE_TOLERANCE = 0.02
RECALL_TOLERANCE = 0.05


def measure() -> SweepReport:
    """Run the deterministic arm on the train split. Free, offline, reproducible."""
    pairs = load_cases(manifest=MANIFEST, split=Split.TRAIN)
    report, _ = sweep(rules_only, pairs, arm="rules_only")
    return report


def load_baseline() -> dict | None:
    if not BASELINE.exists():
        return None
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def save_baseline(report: SweepReport) -> Path:
    BASELINE.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return BASELINE


def check(report: SweepReport, baseline: dict) -> list[str]:
    """Return a list of failures. Empty means the gate passes."""
    failures: list[str] = []

    if not report.meets_constraint:
        failures.append(
            f"SC-002 BREACH: false-approve {report.false_approve_rate:.2%} exceeds the "
            f"{FALSE_APPROVE_LIMIT:.0%} limit "
            f"({report.approved_defective} wrong of {report.approved} approvals). "
            "This is a constraint, not a metric to trade."
        )

    if report.crash_count:
        failures.append(f"SC-007 BREACH: {report.crash_count} crashes (must be 0).")

    was = baseline.get("auto_approve_rate", 0.0)
    now = report.auto_approve_rate
    if now < was - APPROVE_RATE_TOLERANCE:
        failures.append(
            f"REGRESSION: auto-approve {was:.1%} -> {now:.1%} "
            f"(drop of {was - now:.1%}, tolerance {APPROVE_RATE_TOLERANCE:.0%})."
        )

    old_recall = baseline.get("per_issue_recall", {})
    for code, recall in report.to_dict()["per_issue_recall"].items():
        prev = old_recall.get(code)
        if recall is None or prev is None:
            continue
        if recall < prev - RECALL_TOLERANCE:
            failures.append(
                f"REGRESSION: {code} recall {prev:.1%} -> {recall:.1%}. "
                "A per-issue drop the headline numbers would have hidden."
            )

    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description="Eval regression gate. Free, no API calls.")
    ap.add_argument(
        "--update",
        action="store_true",
        help="record the current numbers as the new baseline (do this deliberately)",
    )
    args = ap.parse_args()

    report = measure()
    print(report.render())
    print()

    if args.update:
        path = save_baseline(report)
        print(f"baseline updated -> {path}")
        print("Commit it, and say in the message WHY the numbers moved.")
        raise SystemExit(0)

    baseline = load_baseline()
    if baseline is None:
        print("No baseline recorded. Run: python -m capstone.evals.gate --update")
        raise SystemExit(1)

    print(
        f"baseline: approve {baseline['auto_approve_rate']:.1%}, "
        f"false-approve {baseline['false_approve_rate']:.2%}"
    )

    failures = check(report, baseline)
    if failures:
        print("\n*** GATE FAILED ***")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)

    print("\nGATE PASSED - no regression against the recorded baseline.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()


__all__ = ["check", "load_baseline", "measure", "save_baseline"]
