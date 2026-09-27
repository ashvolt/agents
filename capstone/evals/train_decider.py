"""Fit the logistic decider on the TRAIN split and write it to `capstone/models/`.

    python -m capstone.evals.train_decider                     # cases_large.jsonl, train
    python -m capstone.evals.train_decider --dry-run           # report only, write nothing

What it does, in order:

1. Runs the rules + feature extractor over every train case (`deciders.measure`).
2. Keeps only the files a decider would actually see: not settled by the rules, not
   caught by a guard. Training on the whole set was tried and is worse — most of it is
   files the rules already reject, and the model spends its capacity learning "low DPI
   means defect", which the decider is never asked.
3. Scores the model out-of-fold (5-fold, repeated 5x) so every train case gets a
   probability from a model that never saw it.
4. Sets the approve threshold to **half the lowest out-of-fold score of any defect**.
   Deliberately conservative: an extra escalation costs about $1.40 of reviewer time, a
   false approve costs a misprint, and a threshold fitted on 11 positives has no business
   sitting close to them.
5. Refits on all in-scope train cases and writes plain JSON coefficients.

The holdout is never read here. `load_cases` defaults to train and this script does not
override it.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import RepeatedStratifiedKFold

from capstone.evals.harness import REPO_ROOT, load_cases
from capstone.src.deciders import DEFAULT_MODEL, LogisticModel, guard_reason, measure
from capstone.src.schemas import Split
from capstone.tools.features import ArtworkFeatures

DEFAULT_MANIFEST = REPO_ROOT / "capstone" / "data" / "cases_large.jsonl"

# The five safe-zone measurements. Model selection on train (out-of-fold, same folds):
#
#   features            trained on        lowest defect p   highest clean p   separable
#   all 20              decider scope          0.054             0.519            no
#   all 20, boosted     decider scope          0.015             0.182            no
#   these 5             decider scope          0.352             0.287            yes
#
# Every other measurement is already a rule with a threshold; handing it to the model as
# well only gives 11 positives more ways to overfit.
FEATURES: tuple[str, ...] = (
    "margin_depth_max",
    "margin_span_of_deepest",
    "margin_area_ratio",
    "margin_objects",
    "edge_asymmetry",
)

THRESHOLD_MARGIN = 0.5


def collect(manifest: Path) -> tuple[list[ArtworkFeatures], list[int], dict[str, int]]:
    """Features and labels for the train cases a decider would be asked about."""
    feats: list[ArtworkFeatures] = []
    labels: list[int] = []
    counts = {"train": 0, "settled_by_rules": 0, "guarded": 0, "in_scope": 0}
    for case, label in load_cases(manifest, Split.TRAIN):
        counts["train"] += 1
        settled, _issues, features = measure(case)
        if settled is not None or features is None:
            counts["settled_by_rules"] += 1
            continue
        if guard_reason(features) is not None:
            counts["guarded"] += 1
            continue
        counts["in_scope"] += 1
        feats.append(features)
        labels.append(0 if label.is_clean else 1)
    return feats, labels, counts


def matrix(feats: list[ArtworkFeatures]) -> np.ndarray:
    return np.array(
        [[math.nan if (v := getattr(f, n)) is None else float(v) for n in FEATURES] for f in feats]
    )


def fit(x: np.ndarray, y: np.ndarray) -> tuple[LogisticRegression, np.ndarray, np.ndarray]:
    """Median-impute, standardise, fit. Returns the model plus the transform parameters."""
    impute = np.nanmedian(x, axis=0)
    impute = np.where(np.isnan(impute), 0.0, impute)
    filled = np.where(np.isnan(x), impute, x)
    mean = filled.mean(axis=0)
    scale = filled.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    model = LogisticRegression(C=1.0, max_iter=5000).fit((filled - mean) / scale, y)
    return model, impute, np.stack([mean, scale])


def export(
    model: LogisticRegression,
    impute: np.ndarray,
    norm: np.ndarray,
    threshold: float,
    provenance: dict[str, object],
) -> LogisticModel:
    return LogisticModel(
        name="safe_zone_lr",
        features=FEATURES,
        impute=tuple(float(v) for v in impute),
        mean=tuple(float(v) for v in norm[0]),
        scale=tuple(float(v) for v in norm[1]),
        coef=tuple(float(v) for v in model.coef_[0]),
        intercept=float(model.intercept_[0]),
        threshold=threshold,
        provenance=provenance,
    )


def out_of_fold(feats: list[ArtworkFeatures], y: np.ndarray, repeats: int = 5) -> np.ndarray:
    """Mean p(defect) for each case, from models that never saw it."""
    x = matrix(feats)
    total = np.zeros(len(y))
    seen = np.zeros(len(y))
    folds = RepeatedStratifiedKFold(n_splits=5, n_repeats=repeats, random_state=0)
    for train_idx, test_idx in folds.split(x, y):
        model, impute, norm = fit(x[train_idx], y[train_idx])
        lm = export(model, impute, norm, threshold=0.5, provenance={})
        for i in test_idx:
            total[i] += lm.p_defect(feats[i])
            seen[i] += 1
    return total / np.maximum(seen, 1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fit the CV-feature decider on the train split.")
    ap.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST))
    ap.add_argument("--out", type=str, default=str(DEFAULT_MODEL))
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    manifest = Path(args.manifest)
    feats, labels, counts = collect(manifest)
    y = np.array(labels)
    positives = int(y.sum())
    print(
        f"train cases {counts['train']}: settled by rules {counts['settled_by_rules']}, "
        f"guarded {counts['guarded']}, in scope {counts['in_scope']} ({positives} defective)"
    )
    if positives < 3:
        raise SystemExit("fewer than 3 defective in-scope cases; cannot fit or score")

    oof = out_of_fold(feats, y)
    defect_min = float(oof[y == 1].min())
    clean_max = float(oof[y == 0].max())
    threshold = round(defect_min * THRESHOLD_MARGIN, 4)
    approved = oof < threshold
    print(f"out-of-fold: lowest defect p {defect_min:.3f}, highest clean p {clean_max:.3f}")
    print(f"threshold {threshold:.4f} (= {THRESHOLD_MARGIN} x lowest defect p)")
    print(
        f"  in-scope approve {int(approved.sum())}/{len(y)}, "
        f"false approves {int((approved & (y == 1)).sum())}, "
        f"clean escalated {int(((~approved) & (y == 0)).sum())}"
    )

    model, impute, norm = fit(matrix(feats), y)
    lm = export(
        model,
        impute,
        norm,
        threshold,
        {
            "trained_on": str(manifest.relative_to(REPO_ROOT))
            if manifest.is_absolute()
            else str(manifest),
            "split": "train",
            "in_scope_cases": counts["in_scope"],
            "in_scope_defective": positives,
            "guarded_excluded": counts["guarded"],
            "oof_lowest_defect_p": round(defect_min, 4),
            "oof_highest_clean_p": round(clean_max, 4),
            "threshold_rule": f"{THRESHOLD_MARGIN} x oof_lowest_defect_p",
            "sklearn": sklearn.__version__,
            "fitted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    for name, c in zip(FEATURES, lm.coef, strict=True):
        print(f"  coef {name:24s} {c:+.3f}")

    if args.dry_run:
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lm.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
