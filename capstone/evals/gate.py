"""The CI regression gate.

PLAN.md's L5 exit test is "a red CI build caused by an agent quality regression, not a
code bug." This is the thing that makes that possible.

It runs **the pipeline that ships** — the CV decider (decider.md): rules, OpenCV features,
guards, a JSON logistic model; no API key, no spend — against committed baselines, and
fails the build when quality moves the wrong way. Until 2026-09-24 it gated `rules_only`,
which breaches SC-002 on every 600-case holdout; gating the thing that does not ship
protected nothing.

Two suites, because the evidence behind them differs:

- **synthetic** (`baseline.json`, cases_large train): the full SC-002 constraint applies.
  The shipped pipeline meets it here, so a breach is a regression.
- **real** (`baseline_real.json`, real_art_v3): regression-only. Real artwork does not
  yet meet SC-002 with confidence (real-art.md), so an absolute check would be red on
  every build and teach everyone to ignore it. Instead the build fails if real-art
  false approves rise or approvals drop against the recorded numbers.

Three ways to fail, in order of seriousness:

1. **SC-002 breached.** A false-approve rate above the limit fails outright, whatever the
   approve rate says. It is a constraint, not a metric to trade.
2. **Approve rate regressed** beyond tolerance. Quality went backwards.
3. **Per-issue recall regressed** on any single code beyond tolerance, even if the
   headline numbers held. This catches a change that trades one defect class for another
   — which the aggregate would hide.

Usage:
    python -m capstone.evals.gate                           # synthetic suite
    python -m capstone.evals.gate --suite real              # real-art suite (needs the art)
    python -m capstone.evals.gate --suite real --update     # record a baseline (deliberate)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capstone.evals.harness import load_cases, sweep
from capstone.evals.metrics import FALSE_APPROVE_LIMIT, SweepReport
from capstone.src.deciders import make_cv_decider_triage
from capstone.src.schemas import Split

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALS = REPO_ROOT / "capstone" / "evals"
DATA = REPO_ROOT / "capstone" / "data"

# suite -> (manifest, split, baseline file, whether the absolute SC-002 check applies)
SUITES: dict[str, tuple[Path, Split | None, Path, bool]] = {
    "synthetic": (DATA / "cases_large.jsonl", Split.TRAIN, EVALS / "baseline.json", True),
    "real": (DATA / "real_art_v3.jsonl", None, EVALS / "baseline_real.json", False),
}
BASELINE = SUITES["synthetic"][2]
MANIFEST = SUITES["synthetic"][0]

# Real-art suite: how far the false-approve rate may rise before the build fails. One or
# two cases of noise across library versions (cairosvg, Pillow) should not block a merge.
FALSE_APPROVE_RISE_TOLERANCE = 0.01

# How far a number may drift before the build goes red. Not zero: the deterministic arm
# is bit-identical run to run (SC-008), so any movement here is a real change in
# behaviour rather than noise — but a small allowance keeps a one-case flip from blocking
# an unrelated refactor.
APPROVE_RATE_TOLERANCE = 0.02
RECALL_TOLERANCE = 0.05


def measure(suite: str = "synthetic") -> SweepReport:
    """Run the shipped pipeline on the suite's cases. Free, offline, reproducible."""
    manifest, split, _baseline, _absolute = SUITES[suite]
    pairs = load_cases(manifest=manifest, split=split)
    report, _ = sweep(make_cv_decider_triage(), pairs, arm="cv_decider")
    return report


def load_baseline(suite: str = "synthetic") -> dict | None:
    path = SUITES[suite][2]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(report: SweepReport, suite: str = "synthetic") -> Path:
    path = SUITES[suite][2]
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return path


def check(report: SweepReport, baseline: dict, absolute: bool = True) -> list[str]:
    """Return a list of failures. Empty means the gate passes.

    `absolute=False` (the real-art suite) replaces the SC-002 limit with a no-worse-than-
    recorded check on the false-approve rate.
    """
    failures: list[str] = []

    if not absolute:
        was_fa = baseline.get("false_approve_rate", 0.0)
        if report.false_approve_rate > was_fa + FALSE_APPROVE_RISE_TOLERANCE:
            failures.append(
                f"REGRESSION: false-approve {was_fa:.2%} -> {report.false_approve_rate:.2%} "
                f"(tolerance +{FALSE_APPROVE_RISE_TOLERANCE:.0%}). More wrong approvals "
                "on real artwork."
            )
    elif not report.meets_constraint:
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
    ap.add_argument("--suite", choices=sorted(SUITES), default="synthetic")
    ap.add_argument(
        "--update",
        action="store_true",
        help="record the current numbers as the new baseline (do this deliberately)",
    )
    args = ap.parse_args()

    report = measure(args.suite)
    print(report.render())
    print()

    if args.update:
        path = save_baseline(report, args.suite)
        print(f"baseline updated -> {path}")
        print("Commit it, and say in the message WHY the numbers moved.")
        raise SystemExit(0)

    baseline = load_baseline(args.suite)
    if baseline is None:
        print(
            "No baseline recorded. Run: "
            f"python -m capstone.evals.gate --suite {args.suite} --update"
        )
        raise SystemExit(1)

    print(
        f"baseline: approve {baseline['auto_approve_rate']:.1%}, "
        f"false-approve {baseline['false_approve_rate']:.2%}"
    )

    failures = check(report, baseline, absolute=SUITES[args.suite][3])
    if failures:
        print("\n*** GATE FAILED ***")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)

    print("\nGATE PASSED - no regression against the recorded baseline.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()


__all__ = ["SUITES", "check", "load_baseline", "measure", "save_baseline"]
