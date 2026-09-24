"""Verified fixes — correct what can be corrected, prove it, and price what cannot.

PARKED 2026-09-24: out of scope per brief.md §11 (see scene-narration.md). Kept, tested,
not part of the shipped pipeline.

The business flow already has a step for this: upload -> review -> **proof sent** ->
customer approves. Today an artist makes the proof. This module makes a candidate proof
from the measurements, then re-runs the entire preflight on it. A fix counts only if the
re-check says the problem is gone and nothing new appeared.

Two kinds of output, and the difference matters:

- **applied** — a pixel operation with a deterministic result: mirror the edges out to
  full bleed (the same approach PitStop's add-bleed uses), move an element back inside the
  safe line, thicken a hairline, convert to the product's colour mode. Verified by
  re-running the rules on the result.
- **suggested** — where applying it would be dishonest. Upscaling does not add detail, so
  low resolution becomes "prints sharply up to 1.7 x 1.7 in". Recolouring and resizing
  text are design decisions, so they become exact targets ("#5a5a5a or darker", "x1.43").

Every number in either kind is computed here. A language model downstream may phrase them;
it may not produce them (see `narrate.py`).
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from capstone.src.product_specs import get_spec
from capstone.src.schemas import IssueCode, OrderMetadata, ProductSpec, Severity
from capstone.tools.bucket1_metadata import effective_dpi, inspect_file
from capstone.tools.bucket2_pixels import analyse_pixels, delta_e76
from capstone.tools.scene import Element, SceneDocument, _cluster_keys, extract_scene

# Content is fitted this many pixels inside the safe line, not onto it, so rounding cannot
# put it back.
SAFE_MARGIN_PX = 2

# Shrinking the design by more than this is a design decision, not a correction: it becomes
# a suggestion for the customer instead of an applied fix.
MIN_AUTO_SCALE = 0.85


class Fix(BaseModel):
    """One correction, applied or suggested, with the numbers that justify it."""

    model_config = ConfigDict(frozen=True)

    id: str
    code: IssueCode
    kind: Literal["applied", "suggested"]
    action: str  # plain words, every number already computed
    target: str | None = None  # element id, when the fix is about one element
    params: dict[str, float | str] = Field(default_factory=dict)
    verified: bool | None = None  # applied fixes only: did the re-check clear it?


class FixPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    fixes: tuple[Fix, ...]
    before: tuple[str, ...]  # issue codes present before
    after: tuple[str, ...]  # issue codes present on the rectified image (applied fixes only)
    rectified: Path | None = None
    # distinct design elements before and after; a change means the fix altered the design
    elements_before: int | None = None
    elements_after: int | None = None

    @property
    def proof_ready(self) -> bool:
        """The rectified file may go to the customer as a proof.

        Every applied fix verified, and nothing blocking on the result. One unverified fix
        sends the whole file to a reviewer: a proof that is right in three places and
        wrong in a fourth is still wrong, and a downstream decider approving it does not
        change that (found on holdout_v3, case-00593).

        The element count must also survive. Thickening three closely spaced hairlines
        merged them into one bar: every rule passed, and it was no longer the customer's
        design (same case, seen only when the proof was rendered).
        """
        applied = [f for f in self.fixes if f.kind == "applied"]
        return (
            self.rectified is not None
            and bool(applied)
            and all(f.verified for f in applied)
            and not self.after
            and self.elements_before == self.elements_after
        )


# --------------------------------------------------------------------------------------
# Suggestions — exact targets for what should not be auto-applied
# --------------------------------------------------------------------------------------


def _max_print_size(width_px: int, height_px: int, spec: ProductSpec) -> tuple[float, float]:
    return width_px / spec.min_dpi, height_px / spec.min_dpi


def _contrast_target(colour: str, background: str, min_de: float) -> str | None:
    """The nearest shade of `colour` (toward black or white) that clears the minimum ΔE."""
    rgb = np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], dtype=float)
    bg = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    toward = np.zeros(3) if sum(bg) > 3 * 127 else np.full(3, 255.0)
    for step in range(1, 101):
        candidate = rgb + (toward - rgb) * step / 100
        c = tuple(int(round(v)) for v in candidate)
        if delta_e76(bg, c) >= min_de * 1.1:  # 10% clear of the limit, not on it
            return "#{:02x}{:02x}{:02x}".format(*c)
    return None


# --------------------------------------------------------------------------------------
# Applied fixes — pixel operations
# --------------------------------------------------------------------------------------


def _fit_to_safe(
    arr: np.ndarray, scene: SceneDocument, spec: ProductSpec, grow: int = 0
) -> tuple[np.ndarray, float] | None:
    """Scale the whole design uniformly so it sits inside the safe line.

    Moving elements one at a time was tried first and verified on 40 of 158 moves: many
    elements are wider than the safe area, and text the detector missed arrives as
    separate glyph blobs that a per-element move would scatter. A prepress artist fits the
    layout as a whole, and so does this. Edge-to-edge bands stay where they are - they are
    background, and bleed is their job.

    `grow` pads every element box by that many pixels. Thickening runs first and pushes
    strokes past their original boxes; without the pad the fit moved the box and left the
    grown edge behind as a one-pixel sliver, which the re-check then read as a new hairline.

    Returns the new pixels and the scale used, or None if nothing is in the margin.
    """
    height, width = arr.shape[:2]
    content = [e for e in scene.elements if not e.spans_edge]
    if not any(e.clearance_in < 0 for e in content):
        return None
    ux0 = min(e.px[0] for e in content)
    uy0 = min(e.px[1] for e in content)
    ux1 = max(e.px[2] for e in content)
    uy1 = max(e.px[3] for e in content)

    dpi = scene.dpi
    bleed_x = (width - scene.trim_in[0] * dpi) / 2
    bleed_y = (height - scene.trim_in[1] * dpi) / 2
    inset = spec.safe_zone_in * dpi + SAFE_MARGIN_PX
    sx0, sy0 = bleed_x + inset, bleed_y + inset
    sx1, sy1 = width - bleed_x - inset, height - bleed_y - inset
    scale = min(1.0, (sx1 - sx0) / max(1, ux1 - ux0), (sy1 - sy0) / max(1, uy1 - uy0))

    # centre the scaled content inside the safe area
    cw, ch = (ux1 - ux0) * scale, (uy1 - uy0) * scale
    tx = sx0 + ((sx1 - sx0) - cw) / 2 - ux0 * scale
    ty = sy0 + ((sy1 - sy0) - ch) / 2 - uy0 * scale
    matrix = np.array([[scale, 0, tx], [0, scale, ty]], dtype=np.float32)

    bg = np.array([int(scene.background[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.uint8)
    # Everything inside the design's bounding region moves, not just pixels inside known
    # element boxes: fragments too small to be elements (antialiasing below a text line)
    # were left behind as specks on the proof. Edge-to-edge bands are cut back out.
    pad = grow + 2
    layer = np.zeros((height, width), dtype=np.uint8)
    layer[max(0, uy0 - pad) : uy1 + pad, max(0, ux0 - pad) : ux1 + pad] = 1
    for e in scene.elements:
        if e.spans_edge:
            x0, y0, x1, y1 = e.px
            layer[y0:y1, x0:x1] = 0
    # "Ink" exactly as the scene defines it - any colour cluster but the background's. A
    # looser threshold here left faint antialiased glyph edges behind in the margin, and
    # the re-check (which uses the scene's definition) caught them as still inside.
    keys = _cluster_keys(arr)
    ink = keys != int(np.bincount(keys.ravel()).argmax())
    layer &= ink.astype(np.uint8)

    base = arr.copy()
    base[layer.astype(bool)] = bg
    moved = cv2.warpAffine(arr, matrix, (width, height), flags=cv2.INTER_NEAREST)
    moved_mask = cv2.warpAffine(layer, matrix, (width, height), flags=cv2.INTER_NEAREST)
    base[moved_mask.astype(bool)] = moved[moved_mask.astype(bool)]
    return base, scale


def _thicken(arr: np.ndarray, e: Element, need_px: int, background_key: int) -> bool:
    """Grow one thin stroke to at least `need_px` wide, unless that would touch anything.

    Returns False, leaving the pixels alone, when the thickened stroke would meet a
    neighbour. The first version dilated every same-coloured pixel near the stroke and
    merged closely spaced parallel lines into one bar in 17 of 19 thickening cases: the
    rules passed, and the design was no longer the customer's.
    """
    x0, y0, x1, y1 = e.px
    pad = need_px + 1
    ya, yb = max(0, y0 - pad), min(arr.shape[0], y1 + pad)
    xa, xb = max(0, x0 - pad), min(arr.shape[1], x1 + pad)
    colour = np.array([int(e.colour[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
    region = arr[ya:yb, xa:xb]
    in_box = np.zeros(region.shape[:2], dtype=bool)
    in_box[y0 - ya : y1 - ya, x0 - xa : x1 - xa] = True
    same_colour = np.abs(region.astype(np.int16) - colour).max(axis=2) <= 24
    own = (same_colour & in_box).astype(np.uint8)
    others = (_cluster_keys(region) != background_key) & ~in_box
    grown = cv2.dilate(own, np.ones((need_px, need_px), np.uint8))
    # keep at least one pixel of clear gap to anything else
    if (cv2.dilate(grown, np.ones((3, 3), np.uint8)).astype(bool) & others).any():
        return False
    region[grown.astype(bool)] = colour.astype(np.uint8)
    return True


def _pad_bleed(arr: np.ndarray, order: OrderMetadata, spec: ProductSpec, dpi: float) -> np.ndarray:
    """Mirror the artwork's own edges outward until the canvas carries full bleed."""
    height, width = arr.shape[:2]
    want_w = round((order.width_in + 2 * spec.bleed_in) * dpi)
    want_h = round((order.height_in + 2 * spec.bleed_in) * dpi)
    add_x, add_y = max(0, want_w - width), max(0, want_h - height)
    return cv2.copyMakeBorder(
        arr,
        add_y // 2,
        add_y - add_y // 2,
        add_x // 2,
        add_x - add_x // 2,
        cv2.BORDER_REFLECT,
    )


def _design_elements(scene: SceneDocument) -> int:
    """Distinct non-background, non-text elements: what a fix must not merge or drop."""
    return sum(1 for e in scene.elements if not e.spans_edge and e.shape != "text")


def _recheck(path: Path, order: OrderMetadata) -> tuple[set[str], int]:
    """Blocking codes on the rectified file, plus CONTENT_IN_SAFE_ZONE if anything is still
    inside the keep-out margin.

    The safe-zone rule check is advisory, so "not in the blocking set" would verify every
    move automatically. It is re-measured from a fresh scene of the rectified file instead.
    """
    spec = get_spec(order.product_id)
    issues, meta = inspect_file(path, spec, order)
    if not meta.ok:
        return {IssueCode.UNREADABLE_FILE.value}, 0
    dpi = effective_dpi(meta, spec, order) or float(spec.min_dpi)
    with Image.open(path) as img:
        img.load()
        pixel_issues, boxes = analyse_pixels(img, spec, dpi)
        scene = extract_scene(img, spec, order, dpi, boxes, [])
    found = {i.code.value for i in issues + pixel_issues if i.severity is Severity.BLOCKING}
    if any(e.clearance_in < 0 and not e.spans_edge for e in scene.elements):
        found.add(IssueCode.CONTENT_IN_SAFE_ZONE.value)
    return found, _design_elements(scene)


# --------------------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------------------


def plan_fixes(
    image: Image.Image,
    scene: SceneDocument,
    order: OrderMetadata,
    blocking: set[str],
    out_dir: Path | None = None,
    case_id: str = "rectified",
) -> FixPlan:
    """Build every fix the measurements support, apply the safe ones, and re-check."""
    spec = get_spec(order.product_id)
    dpi = scene.dpi
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
    blocking = set(blocking)
    if any(e.clearance_in < 0 and not e.spans_edge for e in scene.elements):
        blocking.add(IssueCode.CONTENT_IN_SAFE_ZONE.value)  # same test the re-check uses
    fixes: list[Fix] = []
    applied = False

    def add(**kw: object) -> None:
        fixes.append(Fix(id=f"F{len(fixes) + 1}", **kw))  # type: ignore[arg-type]

    # 1. Hairlines -> thicken to the minimum stroke. Before the fit below, which moves
    #    pixels and would leave these boxes pointing at the old positions. The target is
    #    divided by the scale the fit is about to apply, so the fit cannot shrink the
    #    stroke back under the limit.
    dry_run = _fit_to_safe(arr, scene, spec)
    upcoming = dry_run[1] if dry_run and dry_run[1] >= MIN_AUTO_SCALE else 1.0
    need_px = math.ceil(spec.min_stroke_pt / 72.0 * dpi / upcoming + 0.5)
    if IssueCode.THIN_LINES.value in blocking:
        background_key = int(np.bincount(_cluster_keys(arr).ravel()).argmax())
        for e in scene.elements:
            if e.shape not in ("line", "outline") or (e.thickness_pt or 99) >= spec.min_stroke_pt:
                continue
            params = {"from_pt": e.thickness_pt or 0.0, "to_pt": spec.min_stroke_pt}
            if _thicken(arr, e, need_px, background_key):
                applied = True
                add(
                    code=IssueCode.THIN_LINES,
                    kind="applied",
                    target=e.id,
                    action=(
                        f"Thickened {e.id} from {e.thickness_pt:.2f} pt to at least "
                        f"{spec.min_stroke_pt:g} pt."
                    ),
                    params=params,
                )
            else:
                add(
                    code=IssueCode.THIN_LINES,
                    kind="suggested",
                    target=e.id,
                    action=(
                        f"{e.id} is {e.thickness_pt:.2f} pt and needs {spec.min_stroke_pt:g} pt, "
                        "but it sits too close to other artwork to thicken without merging "
                        "into it. Thicken it in your design file and keep a gap around it."
                    ),
                    params=params,
                )

    # 2. Anything inside the keep-out margin -> fit the design inside the safe line.
    thickened = any(f.code is IssueCode.THIN_LINES and f.kind == "applied" for f in fixes)
    fitted = _fit_to_safe(arr, scene, spec, grow=need_px if thickened else 0)
    if fitted is not None:
        new_arr, scale = fitted
        inside = [e.id for e in scene.elements if e.clearance_in < 0 and not e.spans_edge]
        if scale >= MIN_AUTO_SCALE:
            arr = new_arr
            applied = True
            add(
                code=IssueCode.CONTENT_IN_SAFE_ZONE,
                kind="applied",
                target=inside[0],
                action=(
                    (
                        f"Scaled the design to {scale:.0%} and centred it"
                        if scale < 0.995
                        else "Re-centred the design without resizing it"
                    )
                    + f" so every element clears the {spec.safe_zone_in:g} in safe zone "
                    f"({len(inside)} element(s) were inside it). Edge-to-edge backgrounds "
                    "were left in place."
                ),
                params={"scale": round(scale, 3), "elements_inside": len(inside)},
            )
        else:
            add(
                code=IssueCode.CONTENT_IN_SAFE_ZONE,
                kind="suggested",
                target=inside[0],
                action=(
                    f"The design needs to shrink to {scale:.0%} to clear the "
                    f"{spec.safe_zone_in:g} in safe zone, which is too large a change to "
                    "make automatically. Rearrange or resize it so nothing sits within "
                    f"{spec.safe_zone_in:g} in of the cut."
                ),
                params={"scale": round(scale, 3), "elements_inside": len(inside)},
            )

    # 3. Missing bleed -> mirror the edges out, but only if bleed is ALL that is wrong.
    #    Bleed that is genuinely missing is missing equally on every side. If width and
    #    height need different padding, the proportions are off too, and padding to the
    #    right canvas would make every rule pass while the design inside stays stretched.
    #    Found on holdout_v3 (case-00521: MISSING_BLEED + ASPECT_MISMATCH, "verified").
    if IssueCode.MISSING_BLEED.value in blocking:
        width, height = image.size
        pad_x_in = ((order.width_in + 2 * spec.bleed_in) * dpi - width) / 2 / dpi
        pad_y_in = ((order.height_in + 2 * spec.bleed_in) * dpi - height) / 2 / dpi
        if abs(pad_x_in - pad_y_in) <= 1.5 / dpi:  # within rounding of one pixel a side
            arr = _pad_bleed(arr, order, spec, dpi)
            applied = True
            add(
                code=IssueCode.MISSING_BLEED,
                kind="applied",
                action=(
                    f"Extended the artwork to {spec.bleed_in:g} in of bleed on every edge "
                    "by mirroring its own edges outward."
                ),
                params={"bleed_in": spec.bleed_in},
            )
        else:
            add(
                code=IssueCode.MISSING_BLEED,
                kind="suggested",
                action=(
                    f"The file is short on bleed and its proportions do not match the order: "
                    f"it needs {max(0.0, pad_x_in):.3f} in more on the left and right but "
                    f"{max(0.0, pad_y_in):.3f} in on the top and bottom. Resize it to "
                    f"{order.width_in + 2 * spec.bleed_in:g} x "
                    f"{order.height_in + 2 * spec.bleed_in:g} in without stretching, "
                    "then extend the background past the edges."
                ),
                params={"pad_x_in": round(pad_x_in, 3), "pad_y_in": round(pad_y_in, 3)},
            )

    # 4. Colour mode and transparency -> convert on save.
    target_mode = "CMYK" if "CMYK" in spec.accepted_color_modes else "RGB"
    for code in (IssueCode.WRONG_COLOR_MODE, IssueCode.UNINTENDED_TRANSPARENCY):
        if code.value in blocking:
            applied = True
            add(
                code=code,
                kind="applied",
                action=(
                    "Filled transparent areas with the background colour."
                    if code is IssueCode.UNINTENDED_TRANSPARENCY
                    else f"Converted to {target_mode}. Colours may shift slightly; check the proof."
                ),
                params={"mode": target_mode},
            )

    # 5. Suggestions: never auto-applied.
    if IssueCode.LOW_RESOLUTION.value in blocking:
        w_in, h_in = _max_print_size(image.width, image.height, spec)
        need_w = math.ceil((order.width_in + 2 * spec.bleed_in) * spec.min_dpi)
        need_h = math.ceil((order.height_in + 2 * spec.bleed_in) * spec.min_dpi)
        add(
            code=IssueCode.LOW_RESOLUTION,
            kind="suggested",
            action=(
                f"This file prints sharply up to {w_in:.2f} x {h_in:.2f} in including bleed. "
                f"For {order.width_in:g} x {order.height_in:g} in, supply at least "
                f"{need_w} x {need_h} px."
            ),
            params={
                "max_w_in": round(w_in, 2),
                "max_h_in": round(h_in, 2),
                "need_w_px": need_w,
                "need_h_px": need_h,
            },
        )
    if IssueCode.TEXT_TOO_SMALL.value in blocking:
        texts = [e for e in scene.elements if e.shape == "text" and e.height_pt]
        if texts:
            t = min(texts, key=lambda e: e.height_pt or 0)
            factor = spec.min_text_pt / (t.height_pt or 1)
            add(
                code=IssueCode.TEXT_TOO_SMALL,
                kind="suggested",
                target=t.id,
                action=(
                    f"Enlarge {t.id} from {t.height_pt:.1f} pt to at least "
                    f"{spec.min_text_pt:g} pt (x{factor:.2f})."
                ),
                params={
                    "from_pt": t.height_pt or 0.0,
                    "to_pt": spec.min_text_pt,
                    "scale": round(factor, 2),
                },
            )
    if IssueCode.LOW_CONTRAST.value in blocking:
        candidates = [e for e in scene.elements if not e.spans_edge]
        if candidates:
            bg = tuple(int(scene.background[i : i + 2], 16) for i in (1, 3, 5))
            weakest = min(
                candidates,
                key=lambda e: delta_e76(bg, tuple(int(e.colour[i : i + 2], 16) for i in (1, 3, 5))),
            )
            target = _contrast_target(weakest.colour, scene.background, spec.min_contrast_delta_e)
            if target:
                add(
                    code=IssueCode.LOW_CONTRAST,
                    kind="suggested",
                    target=weakest.id,
                    action=(
                        f"Change {weakest.id} from {weakest.colour} to {target} or further "
                        f"from the background {scene.background}."
                    ),
                    params={"from": weakest.colour, "to": target},
                )
    if IssueCode.ASPECT_MISMATCH.value in blocking:
        want = (order.width_in + 2 * spec.bleed_in) / (order.height_in + 2 * spec.bleed_in)
        add(
            code=IssueCode.ASPECT_MISMATCH,
            kind="suggested",
            action=(
                f"Resize the canvas to {want:.3f}:1 "
                f"({order.width_in + 2 * spec.bleed_in:g} x "
                f"{order.height_in + 2 * spec.bleed_in:g} in with bleed) by adding "
                "background or cropping, not by stretching."
            ),
            params={"aspect": round(want, 3)},
        )

    if not applied:
        return FixPlan(
            fixes=tuple(fixes), before=tuple(sorted(blocking)), after=tuple(sorted(blocking))
        )

    # Re-check: the rectified file goes through the same rules as an upload.
    out_dir = out_dir or Path(tempfile.mkdtemp(prefix="rectified-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case_id}.tif"
    Image.fromarray(arr).convert(target_mode).save(path, dpi=(dpi, dpi))
    after, elements_after = _recheck(path, order)
    fixes = [
        f.model_copy(update={"verified": f.code.value not in after}) if f.kind == "applied" else f
        for f in fixes
    ]
    return FixPlan(
        fixes=tuple(fixes),
        before=tuple(sorted(blocking)),
        after=tuple(sorted(after)),
        rectified=path,
        elements_before=_design_elements(scene),
        elements_after=elements_after,
    )


__all__ = ["Fix", "FixPlan", "plan_fixes"]
