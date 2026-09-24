"""Deciders — the step after computer vision, with no vision model in it.

The pipeline this module builds:

    upload -> bucket 1 + 2 rules -> blocking issue?  -> REQUEST_FIX   (rules decide)
                                 -> guard fires?     -> ESCALATE      (nobody can decide)
                                 -> features -> decider -> p(defect)
                                                  p < threshold      -> APPROVE
                                                  otherwise          -> ESCALATE

Three things are deliberate:

1. **The rules keep the blocking checks.** A measured DPI of 150 against a minimum of 300
   is not a probability. The decider is only consulted on files the rules would approve
   or escalate as advisory, which is where both the human cost and the false-approve risk
   sit.
2. **Guards run before the decider and cannot be overridden by it.** They mark files
   where the measurement itself cannot settle the question, so a probability computed
   from that measurement would be confident about nothing. See `guard_reason`.
3. **Every decider has the same shape**: `ArtworkFeatures -> probability of defect`. The
   logistic model here is one implementation. Jev, or Claude, would be another, and the
   eval harness runs any of them unchanged — that is what makes the later comparison a
   config change rather than a rebuild (PLAN.md S11).

The decider never tells a customer to fix something. A high probability escalates to a
human; only a rule measurement produces a REQUEST_FIX. The model is fitted to synthetic
art and is good enough to route work, not to author customer-facing claims.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Protocol

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from capstone.evals.baselines import _customer_message
from capstone.src.product_specs import UnknownProductError, get_spec
from capstone.src.schemas import (
    EscalationReason,
    Evidence,
    Issue,
    IssueCode,
    PreflightCase,
    Severity,
    Verdict,
    VerdictType,
)
from capstone.tools.bucket1_metadata import effective_dpi, inspect_file
from capstone.tools.bucket2_pixels import analyse_pixels
from capstone.tools.features import ArtworkFeatures, extract_features

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_MODEL = MODELS_DIR / "safe_zone_lr.json"


class Decider(Protocol):
    """Anything that turns a feature vector into a probability that the file is defective."""

    name: str

    def p_defect(self, features: ArtworkFeatures) -> float: ...


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------


def guard_reason(features: ArtworkFeatures) -> str | None:
    """Why this file cannot be decided from its measurements, or None if it can."""
    found = _guard(features)
    return found[1] if found else None


def guard_issue(features: ArtworkFeatures) -> Issue | None:
    """The guard as an advisory finding a reviewer can act on, or None.

    An escalation that only says "undecidable" leaves the reviewer to rediscover what the
    pipeline already knew. Naming the code also lets per-issue recall credit the catch.
    """
    found = _guard(features)
    if found is None:
        return None
    code, reason = found
    return Issue(
        code=code,
        severity=Severity.ADVISORY,
        message=f"Needs a human look: {reason}.",
        evidence=Evidence(note=f"guard: {reason}"),
    )


def _guard(features: ArtworkFeatures) -> tuple[IssueCode, str] | None:
    """The guards, in order. Returns the issue code each one is about and why.

    - **No text found.** The detector's failure modes all fail toward finding nothing, so
      an empty result is ambiguous between "no text" and "text I could not see"
      (results.md S4.3). Policy since 2026-09-23; a decider does not get to relax it.
    - **A measurement within half a pixel of its limit.** Rasterising rounds to the
      nearest pixel, so the true width could sit on either side (limits.md S4). On the
      train split this is exactly one THIN_LINES defect at 1.1x and two clean files at
      0.9x, all measuring 1.00x. No feature separates them, because the pixels do not.
    - **An element past the cut line** (margin depth above 1.0). Geometry, not judgement.
    - **An element merged into a background band.** Its outer edge is inside the band, so
      how far it reaches toward the blade is not in the pixels (features.py
      `_band_bumps`). Found on the shifted holdout, where it hid 7 of 10 top/bottom
      intrusions.
    """
    if features.text_lines == 0:
        return (
            IssueCode.TEXT_TOO_SMALL,
            "text detector found no text; its misses cannot be told from absence",
        )
    if features.margin_depth_max > 1.0:
        # Past the trim line is a measurement, not a judgement. Left to the model, it was
        # learned from synthetic "clean" files whose caption runs off the canvas (a label
        # flaw, limits.md S11), and the refit approved two real intrusions at 1.1x.
        return (
            IssueCode.CONTENT_IN_SAFE_ZONE,
            f"an element reaches {features.margin_depth_max:.2f} safe-zone widths into the "
            "margin, past the cut line",
        )
    if features.band_protrusions:
        return (
            IssueCode.CONTENT_IN_SAFE_ZONE,
            "an element is merged into a background band at the edge; its position "
            "relative to the cut cannot be measured",
        )
    for code, name, value, half_pixel in (
        (IssueCode.THIN_LINES, "stroke width", features.stroke_ratio, features.stroke_half_pixel),
        (IssueCode.TEXT_TOO_SMALL, "text height", features.text_ratio, features.text_half_pixel),
    ):
        if value is not None and half_pixel is not None and value < 1.0 + half_pixel:
            return (
                code,
                f"{name} measures {value:.2f}x the minimum, within half a pixel of the "
                "limit; the true value could be on either side",
            )
    return None


# --------------------------------------------------------------------------------------
# The logistic decider
# --------------------------------------------------------------------------------------


class LogisticModel(BaseModel):
    """A fitted logistic regression, stored as numbers rather than a pickle.

    Plain JSON on purpose: it is reviewable in a diff, it loads without scikit-learn, and
    it cannot execute code on load the way a pickle can. Training lives in
    `capstone/evals/train_decider.py`; this class only does inference.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    features: tuple[str, ...]
    impute: tuple[float, ...]  # median per feature, for None values
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    threshold: float = Field(gt=0.0, lt=1.0)  # approve strictly below this p(defect)
    provenance: dict[str, object] = Field(default_factory=dict)

    def p_defect(self, features: ArtworkFeatures) -> float:
        z = self.intercept
        for i, name in enumerate(self.features):
            raw = getattr(features, name)
            value = self.impute[i] if raw is None else float(raw)
            z += self.coef[i] * (value - self.mean[i]) / self.scale[i]
        # numerically safe sigmoid
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    @classmethod
    def load(cls, path: Path = DEFAULT_MODEL) -> LogisticModel:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------------------


def measure(case: PreflightCase) -> tuple[Verdict | None, list[Issue], ArtworkFeatures | None]:
    """Run the rules and the feature extractor in one pass over the file.

    Returns `(final_verdict, issues, features)`. `final_verdict` is set when the rules
    alone settle the file — unsupported product, unreadable file, or a blocking issue —
    and in that case no decider is consulted.
    """
    try:
        spec = get_spec(case.order.product_id)
    except UnknownProductError:
        return (
            Verdict(
                verdict=VerdictType.ESCALATE,
                confidence=0.0,
                escalation_reason=EscalationReason.UNSUPPORTED_INPUT,
            ),
            [],
            None,
        )

    checks = ["bucket1_metadata"]
    issues, meta = inspect_file(case.image_path, spec, case.order)
    if not meta.ok:
        return (
            Verdict(
                verdict=VerdictType.ESCALATE,
                confidence=1.0,
                issues=issues,
                escalation_reason=EscalationReason.UNSUPPORTED_INPUT,
                checks_completed=checks,
            ),
            issues,
            None,
        )

    dpi = effective_dpi(meta, spec, case.order) or float(spec.min_dpi)
    features: ArtworkFeatures | None = None
    try:
        with Image.open(case.image_path) as img:
            img.load()
            pixel_issues, boxes = analyse_pixels(img, spec, dpi)
            issues = issues + pixel_issues
            advisory = sum(i.severity is Severity.ADVISORY for i in issues)
            features = extract_features(img, meta, spec, case.order, dpi, boxes, advisory)
        checks += ["bucket2_pixels", "cv_features"]
    except OSError as exc:
        issues = issues + [
            Issue(
                code=IssueCode.UNREADABLE_FILE,
                severity=Severity.BLOCKING,
                message="The artwork could not be fully decoded for pixel analysis.",
                evidence=Evidence(note=f"{type(exc).__name__}: {exc}"),
            )
        ]

    blocking = [i for i in issues if i.severity is Severity.BLOCKING]
    if blocking:
        return (
            Verdict(
                verdict=VerdictType.REQUEST_FIX,
                confidence=0.95,
                issues=issues,
                customer_message=_customer_message(blocking, case),
                checks_completed=checks,
            ),
            issues,
            features,
        )
    return None, issues, features


def decide(case: PreflightCase, decider: Decider, threshold: float) -> tuple[Verdict, float | None]:
    """Triage one file. Returns the verdict and the decider's p(defect), if it was asked."""
    settled, issues, features = measure(case)
    if settled is not None:
        return settled, None
    checks = ["bucket1_metadata", "bucket2_pixels", "cv_features"]
    assert features is not None  # measure() only returns unsettled with features

    advisory = [i for i in issues if i.severity is Severity.ADVISORY]

    guarded = guard_issue(features)
    if guarded is not None:
        others = [i for i in advisory if i.code is not guarded.code]
        return (
            Verdict(
                verdict=VerdictType.ESCALATE,
                confidence=0.5,
                issues=[guarded, *others],
                escalation_reason=EscalationReason.JUDGEMENT_WITHOUT_CORROBORATION,
                checks_completed=checks + ["guard"],
            ),
            None,
        )

    p = decider.p_defect(features)
    if p < threshold:
        return (
            Verdict(
                verdict=VerdictType.APPROVE,
                confidence=round(1.0 - p, 4),
                checks_completed=checks + [decider.name],
            ),
            p,
        )

    # Escalations carry the reasoning a reviewer needs, not just a number (PLAN.md S6.5).
    evidence = Issue(
        code=IssueCode.CONTENT_IN_SAFE_ZONE,
        severity=Severity.ADVISORY,
        message=(
            "An element reaches into the keep-out margin near the cut line. A reviewer "
            "should confirm whether it will be trimmed."
        ),
        evidence=Evidence(
            measured=features.margin_depth_max,
            required=1.0,
            unit="safe-zone depth (1.0 = trim line)",
            note=(
                f"{decider.name} p(defect)={p:.2f} against threshold {threshold:.2f}; "
                f"{features.margin_objects} partial-edge object(s) in the margin, deepest "
                f"spans {features.margin_span_of_deepest:.0%} of its edge"
            ),
        ),
    )
    others = [i for i in advisory if i.code is not IssueCode.CONTENT_IN_SAFE_ZONE]
    return (
        Verdict(
            verdict=VerdictType.ESCALATE,
            confidence=round(p, 4),
            issues=[evidence, *others],
            escalation_reason=EscalationReason.LOW_CONFIDENCE,
            checks_completed=checks + [decider.name],
        ),
        p,
    )


def make_cv_decider_triage(model: LogisticModel | None = None):
    """Harness adapter: `PreflightCase -> Verdict` using the stored logistic decider."""
    model = model or LogisticModel.load()

    def triage(case: PreflightCase) -> Verdict:
        return decide(case, model, model.threshold)[0]

    return triage


__all__ = [
    "DEFAULT_MODEL",
    "Decider",
    "LogisticModel",
    "decide",
    "guard_issue",
    "guard_reason",
    "make_cv_decider_triage",
    "measure",
]
