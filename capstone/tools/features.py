"""CV feature extraction — the image, described as numbers a decider can read.

The decider (`capstone/src/deciders.py`) never sees pixels. It sees this vector, so
everything a decision could depend on has to be measured here first. That is the whole
trade of skipping the vision model: whatever this module cannot describe, nothing
downstream can know.

Two rules shape every feature:

1. **Ratios against the spec, not raw values.** `stroke_ratio = measured / minimum`, so
   1.0 is the limit for every product and a decider trained on stickers is not
   re-learning thresholds for banners. Below 1.0 is past the limit.
2. **No orientation.** The synthetic generator always places a safe-zone intrusion on the
   left edge. A feature that says "left" would let a classifier learn the generator
   rather than the defect. Edge statistics are sorted; margin objects are described by
   depth and span, never by which side they sit on.

The margin-object analysis is the one genuinely new measurement here, and the reason
OpenCV is in the stack: `connectedComponentsWithStats` gives every object's bounding box
and area in one C pass, which the hand-rolled run labelling in `text_detect` does not.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict

from capstone.src.schemas import OrderMetadata, ProductSpec
from capstone.tools.bucket1_metadata import FileMetadata, measure_bleed_in
from capstone.tools.bucket2_pixels import (
    PT_PER_INCH,
    measure_contrast,
    measure_min_stroke_px,
    measure_safe_zone,
)
from capstone.tools.text_detect import TextBox

# Per-channel difference from the background colour above which a pixel is "something".
# Generous on purpose: antialiasing and 16-level quantisation noise sit well below it.
FOREGROUND_CHANNEL_DELTA = 24

# Components smaller than this are speckle, not an element anyone would care about.
MARGIN_MIN_AREA_PX = 4

# An object covering at least this share of the edge it sits against is a band running
# off the edge — a background or border, which is exactly what bleed is for. Anything
# shorter than that and still inside the keep-out margin is an element near the blade.
FULL_EDGE_SPAN = 0.9


class ArtworkFeatures(BaseModel):
    """Everything a decider is allowed to know about one file.

    `None` means "not measurable on this file", which is different from zero and must
    stay different: no text found is not the same as text at size zero.
    """

    model_config = ConfigDict(frozen=True)

    # bucket 1 — geometry, as ratios to the spec (>= 1.0 is within spec)
    dpi_ratio: float | None
    bleed_ratio: float | None
    aspect_deviation_ratio: float | None  # deviation / tolerance, <= 1.0 is within spec

    # bucket 2 — pixel measurements, as ratios to the spec (>= 1.0 is within spec)
    stroke_ratio: float | None
    contrast_ratio: float | None
    text_ratio: float | None
    text_lines: int

    # half a pixel, in the same ratio units as the measurement above it. Rasterising
    # rounds to the nearest pixel, so a measurement closer than this to 1.0 cannot say
    # which side of the limit the true width is on (limits.md S4). Deciders use these as
    # guards, not as evidence.
    stroke_half_pixel: float | None
    text_half_pixel: float | None

    # safe zone — the existing edge statistics, sorted so orientation cannot leak
    edge_asymmetry: float | None
    edge_coverage_max: float | None
    edge_coverage_min: float | None
    interior_coverage: float | None

    # safe zone — margin objects (OpenCV). Depth is in safe-zone units measured from the
    # inner edge of the keep-out margin: 0 = just touching it, 1.0 = reaching the trim
    # line, above 1.0 = past the cut.
    margin_objects: int
    margin_depth_max: float
    margin_span_of_deepest: float
    margin_area_ratio: float
    safe_zone_px: float

    # how many advisory findings the rules raised — the rules' own uncertainty
    advisory_count: int

    def vector(self) -> list[float]:
        """Fixed-order numeric vector. `None` becomes NaN for the model to handle."""
        return [
            float("nan") if (v := getattr(self, name)) is None else float(v)
            for name in FEATURE_NAMES
        ]


FEATURE_NAMES: tuple[str, ...] = tuple(ArtworkFeatures.model_fields)


# --------------------------------------------------------------------------------------
# Margin objects
# --------------------------------------------------------------------------------------


def _background_rgb(arr: np.ndarray) -> np.ndarray:
    """Most common colour, quantised to 16 levels so antialiasing cannot split it."""
    flat = arr.reshape(-1, 3)
    keys = ((flat >> 4).astype(np.int32) * np.array([256, 16, 1], dtype=np.int32)).sum(axis=1)
    background_key = int(np.bincount(keys).argmax())
    return flat[keys == background_key].mean(axis=0)


def measure_margin_objects(
    image: Image.Image,
    spec: ProductSpec,
    order: OrderMetadata,
    dpi: float,
    exclude: list[TextBox] | None = None,
) -> dict[str, float]:
    """Describe every element that reaches into the keep-out margin.

    The keep-out margin is the band between the canvas edge and a line `safe_zone_in`
    inside the trim. Bleed sits in it by design, so ink there is normal; what matters is
    *what* the ink is. A background runs the full length of an edge. A logo that drifted
    toward the blade does not.

    Trim is located from the ordered size rather than from the spec's bleed, so a file
    short on bleed still gets its cut line in the right place.

    Detected text is excluded, as it is from the stroke measurement. That is a concession
    to the dataset, not a claim about print: the generator's label text overruns the trim
    on small products and those files are labelled clean (limits.md S11). Leaving text in
    would teach a classifier that type at the blade is harmless. Leaving it out means text
    at the blade is caught by nothing - a known gap, recorded rather than learned.
    """
    empty = {
        "margin_objects": 0,
        "margin_depth_max": 0.0,
        "margin_span_of_deepest": 0.0,
        "margin_area_ratio": 0.0,
        "safe_zone_px": 0.0,
    }
    safe_px = spec.safe_zone_in * dpi
    if dpi <= 0 or safe_px < 1:
        return empty

    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = arr.shape[:2]
    if height == 0 or width == 0:
        return empty

    bleed_x = max(0.0, (width - order.width_in * dpi) / 2)
    bleed_y = max(0.0, (height - order.height_in * dpi) / 2)
    inset_x = bleed_x + safe_px
    inset_y = bleed_y + safe_px
    if width <= 2 * inset_x or height <= 2 * inset_y:
        return {**empty, "safe_zone_px": round(safe_px, 2)}

    background = _background_rgb(arr)
    diff = np.abs(arr.astype(np.int16) - background.astype(np.int16)).max(axis=2)
    foreground = (diff > FOREGROUND_CHANNEL_DELTA).astype(np.uint8)
    for box in exclude or []:
        foreground[max(0, box.y0 - 1) : box.y1 + 1, max(0, box.x0 - 1) : box.x1 + 1] = 0

    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(foreground, connectivity=8)

    ring_area = width * height - (width - 2 * inset_x) * (height - 2 * inset_y)
    objects = 0
    deepest = 0.0
    deepest_span = 0.0
    area_in_margin = 0.0

    for idx in range(1, count):  # 0 is the background label
        x, y, w, h, area = (int(v) for v in stats[idx])
        if area < MARGIN_MIN_AREA_PX:
            continue

        # Distance from the object to each canvas edge, and how much of that edge it
        # covers. The side it reaches deepest into the margin is the one that matters.
        sides = (
            (x, inset_x, h / height),
            (width - (x + w), inset_x, h / height),
            (y, inset_y, w / width),
            (height - (y + h), inset_y, w / width),
        )
        near = [(side_inset - d) / safe_px for d, side_inset, _ in sides if d < side_inset]
        spans = [span for d, side_inset, span in sides if d < side_inset]
        # A band spanning ANY edge it sits on is background, even though it is short
        # along the perpendicular edge it also touches at the corner. Judging a corner
        # band by its shorter side called every full-width border an intrusion.
        if not near or max(spans) >= FULL_EDGE_SPAN:
            continue
        best_depth = max(near)
        best_span = spans[near.index(best_depth)]

        objects += 1
        area_in_margin += area
        if best_depth > deepest:
            deepest, deepest_span = best_depth, best_span

    return {
        "margin_objects": objects,
        "margin_depth_max": round(deepest, 4),
        "margin_span_of_deepest": round(deepest_span, 4),
        "margin_area_ratio": round(area_in_margin / ring_area, 6) if ring_area > 0 else 0.0,
        "safe_zone_px": round(safe_px, 2),
    }


# --------------------------------------------------------------------------------------
# The whole vector
# --------------------------------------------------------------------------------------


def _ratio(measured: float | None, required: float) -> float | None:
    if measured is None or required <= 0 or not math.isfinite(measured):
        return None
    return round(measured / required, 4)


def extract_features(
    image: Image.Image,
    meta: FileMetadata,
    spec: ProductSpec,
    order: OrderMetadata,
    dpi: float,
    boxes: list[TextBox],
    advisory_count: int,
) -> ArtworkFeatures:
    """Measure one decoded file. Pure: same inputs, same vector, no IO beyond `image`."""
    # bucket 1
    bleed_present = measure_bleed_in(meta, order)
    aspect = meta.aspect_ratio
    expected_aspect = (order.width_in + 2 * spec.bleed_in) / (order.height_in + 2 * spec.bleed_in)
    aspect_dev = abs(aspect / expected_aspect - 1.0) if aspect else None

    # bucket 2
    stroke_px = measure_min_stroke_px(image, exclude=boxes)
    stroke_pt = stroke_px / dpi * PT_PER_INCH if stroke_px and dpi > 0 else None
    contrast = measure_contrast(image)
    text_pt = min(b.height_pt(dpi) for b in boxes) if boxes and dpi > 0 else None

    # safe zone
    sz = measure_safe_zone(image, spec, dpi)
    if sz.get("measurable"):
        coverage = sorted(sz["edge_ink_coverage"].values())  # type: ignore[union-attr]
        edge = {
            "edge_asymmetry": float(sz["opposing_edge_asymmetry"]),  # type: ignore[arg-type]
            "edge_coverage_max": coverage[-1],
            "edge_coverage_min": coverage[0],
            "interior_coverage": float(sz["interior_ink_coverage"]),  # type: ignore[arg-type]
        }
    else:
        edge = dict.fromkeys(
            ("edge_asymmetry", "edge_coverage_max", "edge_coverage_min", "interior_coverage")
        )

    return ArtworkFeatures(
        dpi_ratio=_ratio(dpi, float(spec.min_dpi)),
        bleed_ratio=_ratio(bleed_present, spec.bleed_in) if spec.bleed_in > 0 else None,
        aspect_deviation_ratio=_ratio(aspect_dev, spec.aspect_tolerance),
        stroke_ratio=_ratio(stroke_pt, spec.min_stroke_pt),
        contrast_ratio=_ratio(contrast[0] if contrast else None, spec.min_contrast_delta_e),
        text_ratio=_ratio(text_pt, spec.min_text_pt),
        text_lines=len(boxes),
        stroke_half_pixel=_ratio(0.5 / dpi * PT_PER_INCH, spec.min_stroke_pt) if dpi > 0 else None,
        text_half_pixel=_ratio(0.5 / dpi * PT_PER_INCH, spec.min_text_pt) if dpi > 0 else None,
        **edge,
        **measure_margin_objects(image, spec, order, dpi, exclude=boxes),
        advisory_count=advisory_count,
    )


__all__ = [
    "FEATURE_NAMES",
    "FULL_EDGE_SPAN",
    "ArtworkFeatures",
    "extract_features",
    "measure_margin_objects",
]
