"""The whole no-vision pipeline for one file, in one call.

PARKED 2026-09-24: out of scope per brief.md §11 (see scene-narration.md). Kept, tested,
not part of the shipped pipeline.

    rules -> decider -> scene -> fixes -> re-checked proof -> decider on the proof

Every experiment in decider.md and scene-narration.md ran some version of this by hand.
It lives here so evals, the narration benchmark, and the real-artwork run all measure
the same thing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from capstone.src.deciders import LogisticModel, decide
from capstone.src.product_specs import get_spec
from capstone.src.schemas import PreflightCase, Severity, Verdict, VerdictType
from capstone.tools.bucket1_metadata import effective_dpi, inspect_file
from capstone.tools.bucket2_pixels import analyse_pixels
from capstone.tools.fixes import FixPlan, plan_fixes
from capstone.tools.scene import SceneDocument, describe


class Outcome(BaseModel):
    """Everything the pipeline decided about one file."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    case_id: str
    verdict: Verdict  # on the upload
    scene: SceneDocument | None = None  # None when the file never decoded
    plan: FixPlan | None = None  # None when the upload was approved as-is
    proof_verdict: Verdict | None = None  # decider on the corrected proof, if proof-ready

    @property
    def print_ready(self) -> bool:
        """Approved as uploaded, or a verified proof the decider also approves."""
        if self.verdict.verdict is VerdictType.APPROVE:
            return True
        return self.proof_verdict is not None and self.proof_verdict.verdict is VerdictType.APPROVE


def run(case: PreflightCase, model: LogisticModel, out_dir: Path | None = None) -> Outcome:
    """Triage one file end to end. Never raises for a bad file; a decode error stops early."""
    verdict, _ = decide(case, model, model.threshold)
    spec = get_spec(case.order.product_id)
    issues, meta = inspect_file(case.image_path, spec, case.order)
    if not meta.ok:
        return Outcome(case_id=case.case_id, verdict=verdict)
    dpi = effective_dpi(meta, spec, case.order) or float(spec.min_dpi)
    try:
        with Image.open(case.image_path) as img:
            img.load()
            pixel_issues, boxes = analyse_pixels(img, spec, dpi)
            everything = issues + pixel_issues
            scene = describe(img, spec, case.order, dpi, boxes, everything)
            if verdict.verdict is VerdictType.APPROVE:
                return Outcome(case_id=case.case_id, verdict=verdict, scene=scene)
            blocking = {i.code.value for i in everything if i.severity is Severity.BLOCKING}
            out_dir = out_dir or Path(tempfile.mkdtemp(prefix="proofs-"))
            plan = plan_fixes(img, scene, case.order, blocking, out_dir, case.case_id)
    except OSError:
        return Outcome(case_id=case.case_id, verdict=verdict)

    proof_verdict = None
    if plan.proof_ready and plan.rectified is not None:
        proof_case = PreflightCase(
            case_id=case.case_id, image_path=plan.rectified, order=case.order
        )
        proof_verdict, _ = decide(proof_case, model, model.threshold)
    return Outcome(
        case_id=case.case_id, verdict=verdict, scene=scene, plan=plan, proof_verdict=proof_verdict
    )


__all__ = ["Outcome", "run"]
