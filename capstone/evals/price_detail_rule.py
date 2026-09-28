"""Price a spec option: "detail shorter than L at print size is not judged as a stroke".

    python -m capstone.evals.price_detail_rule --lengths 0 0.02 0.04 0.08

On AI-generated art most clean files are rejected for sub-minimum *detail* the image
carries itself: speckle, hairline texture (ai-art.md section 4). Whether such detail may
print is a spec decision. This measures what each candidate would buy and cost, with the
code that would ship (bucket2_pixels.STROKE_MIN_DETAIL_IN), on every set at hand:

- auto-approve and wrong approvals by label, with the exact 95% bound;
- files approved that were not approved at L = 0, and how many are labelled defective;
- L = 0 must reproduce each set's published score, or the comparison means nothing.

Labels cover injected defects only. A clean-labelled file approved *because* of the
rule carries real sub-minimum detail shorter than L: that count is the spec risk, and
no label can say whether it matters on press.

Diagnostic, not a sealed score. Every set here is already spent. A rule adopted from
this gets a fresh sealed set.
"""

from __future__ import annotations

import argparse
import json
from multiprocessing import Pool
from pathlib import Path

from capstone.evals.harness import load_cases, run_one, score
from capstone.src.deciders import make_cv_decider_triage
from capstone.src.schemas import Split
from capstone.tools import bucket2_pixels

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parent / "runs"

# (name, manifest, split, published cv_decider approve rate and wrong approvals)
SETS = [
    ("ai_art_v1", "ai_art_v1.jsonl", None, "54.3% / 0 of 339"),
    ("ai_art_v2", "ai_art_v2.jsonl", None, "57.1% / 0 of 354"),
    ("real_art_v5", "real_art_v5.jsonl", None, "78.7% / 0 of 500"),
    ("mistakes_v2", "mistakes_v2.jsonl", None, "87.0% / 0 of 240"),
    ("synthetic (gate)", "cases_large.jsonl", Split.TRAIN, "84.8% / 0 of 167"),
]

_TRIAGE = None


def _init(length_in: float) -> None:
    global _TRIAGE
    bucket2_pixels.STROKE_MIN_DETAIL_IN = length_in
    _TRIAGE = make_cv_decider_triage()


def _one(pair):  # noqa: ANN001, ANN202 - (case, label) in, RunResult out
    return run_one(_TRIAGE, *pair)


def price(manifest: str, split: Split | None, length_in: float, workers: int):  # noqa: ANN201
    pairs = load_cases(DATA / manifest, split=split)
    with Pool(workers, initializer=_init, initargs=(length_in,)) as pool:
        results = pool.map(_one, pairs, chunksize=4)
    return score(results, arm=f"cv_decider_detail_{length_in:g}in"), results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lengths", type=float, nargs="+", default=[0.0, 0.02, 0.04, 0.08])
    ap.add_argument("--sets", nargs="+", default=[name for name, *_ in SETS])
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    rows = []
    for name, manifest, split, published in SETS:
        if name not in args.sets:
            continue
        base_approved: set[str] | None = None
        for length in args.lengths:
            report, results = price(manifest, split, length, args.workers)
            approved = {r.case_id for r in results if r.approved}
            wrong = {r.case_id for r in results if r.is_false_approve}
            if base_approved is None:
                base_approved = approved
            new = approved - base_approved
            row = {
                "set": name,
                "detail_in": length,
                "published_at_0": published,
                "auto_approve_rate": report.auto_approve_rate,
                "approved": report.approved,
                "wrong_approvals": report.approved_defective,
                "bound_95": report.false_approve_ci_upper,
                "newly_approved": len(new),
                "newly_approved_wrong": len(new & wrong),
            }
            rows.append(row)
            print(
                f"{name:18s} L={length:<5g} approve {report.auto_approve_rate:6.1%}  wrong "
                f"{report.approved_defective:>2} of {report.approved:<4} bound "
                f"{report.false_approve_ci_upper:5.1%}  new approvals {len(new):>3} "
                f"(wrong {len(new & wrong)})   published at 0: {published}",
                flush=True,
            )
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "price_detail_rule.json"
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
