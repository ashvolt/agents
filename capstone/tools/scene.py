"""Scene documents — the image, described as a list of elements a text model can reason about.

The decider (`deciders.py`) reads five numbers. A model asked to *explain* a file, or to
suggest how to fix it, needs more than that: what is on the artwork, where each thing sits
relative to the cut, and what is wrong with it. This module builds that description, and
then does the part that makes it trustworthy:

**It proves the description is complete.** `render_scene` redraws the artwork from the
description alone, and `fidelity` compares the redraw against the real pixels. A text model
can only reason about what the description contains; fidelity measures how much that is.
A description that cannot reproduce the artwork is a description a model should not be
trusted to reason from, and the pipeline can say so with a number instead of hoping.

Coordinates are in inches, measured from the **trim line** — the frame a print operator
thinks in — rather than in pixels from the canvas corner. Negative means outside the trim,
in the bleed. Every element also carries its distance to the safe line, so "is this too
close to the blade" is a field lookup, not a geometry problem for the model.

Built for flat-colour artwork, which is what the synthetic set contains. Photographs and
gradients will not decompose into a handful of flat elements; fidelity will be low on
them, and that is the intended signal, not a bug.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict

from capstone.src.schemas import Issue, OrderMetadata, ProductSpec
from capstone.tools.text_detect import TextBox

# Colour clusters are 16 levels per channel, the same quantisation the contrast check uses.
_QUANT_SHIFT = 4

# An element must hold at least this many pixels. Below it is antialiasing and speckle.
MIN_ELEMENT_PX = 12

# Fill ratio (pixels / bounding-box area) at or above which an element is a solid shape.
SOLID_FILL = 0.9

# A shape this many times longer than it is thick is a line.
LINE_ASPECT = 8.0

# Polygon simplification tolerance, in pixels. One pixel keeps the redraw faithful while
# collapsing the staircase of a rasterised curve into a handful of vertices.
POLYGON_EPSILON_PX = 1.0

Shape = Literal["rect", "line", "outline", "blob", "text"]


class Element(BaseModel):
    """One thing on the artwork."""

    model_config = ConfigDict(frozen=True)

    id: str
    shape: Shape
    colour: str  # hex, e.g. "#1f1f1f"
    # bounding box in inches from the trim line's top-left; negative = in the bleed
    x_in: float
    y_in: float
    w_in: float
    h_in: float
    # nearest distance to the safe line, in inches. Negative = inside the keep-out margin.
    clearance_in: float
    # which edge that nearest distance is measured to
    nearest_edge: Literal["left", "right", "top", "bottom"]
    spans_edge: bool  # runs the full length of the edge it sits on: a background band
    thickness_pt: float | None = None  # lines and outlines only
    height_pt: float | None = None  # text only
    # outlines and blobs: rings of (x, y) in inches from trim, outer ring first, then holes.
    # The SVG-path equivalent. Rects, lines and text are fully described by their box.
    rings: tuple[tuple[tuple[float, float], ...], ...] | None = None
    # pixel box, kept for rendering and for fixes; not for the model
    px: tuple[int, int, int, int]


class SceneDocument(BaseModel):
    """What a text model receives instead of pixels."""

    model_config = ConfigDict(frozen=True)

    product: str
    trim_in: tuple[float, float]
    bleed_in: float
    safe_zone_in: float
    dpi: float
    background: str
    elements: tuple[Element, ...]
    findings: tuple[dict[str, object], ...]  # rule results: code, severity, measured, required
    fidelity: float | None = None  # filled in by `describe`; see `fidelity`

    def for_model(self) -> dict[str, object]:
        """The document as sent to a model: pixel boxes dropped, nothing else changed."""
        data = self.model_dump(mode="json")
        for element in data["elements"]:
            element.pop("px", None)
        return data


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------


def _hex(rgb: np.ndarray) -> str:
    r, g, b = (int(round(float(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _cluster_keys(arr: np.ndarray) -> np.ndarray:
    q = (arr >> _QUANT_SHIFT).astype(np.int32)
    return q[..., 0] * 256 + q[..., 1] * 16 + q[..., 2]


def extract_scene(
    image: Image.Image,
    spec: ProductSpec,
    order: OrderMetadata,
    dpi: float,
    boxes: list[TextBox],
    issues: list[Issue],
) -> SceneDocument:
    """Decompose flat-colour artwork into elements, positioned against the trim."""
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = arr.shape[:2]
    keys = _cluster_keys(arr)
    counts = np.bincount(keys.ravel())
    background_key = int(counts.argmax())
    background = arr[keys == background_key].mean(axis=0)

    bleed_x = (width - order.width_in * dpi) / 2
    bleed_y = (height - order.height_in * dpi) / 2
    safe_px = spec.safe_zone_in * dpi

    def to_trim(x: int, y: int) -> tuple[float, float]:
        return (x - bleed_x) / dpi, (y - bleed_y) / dpi

    def clearance(x: int, y: int, w: int, h: int) -> tuple[float, str]:
        # distance from each side of the box to the safe line on that side, in pixels
        gaps = {
            "left": x - (bleed_x + safe_px),
            "right": (width - bleed_x - safe_px) - (x + w),
            "top": y - (bleed_y + safe_px),
            "bottom": (height - bleed_y - safe_px) - (y + h),
        }
        edge = min(gaps, key=gaps.__getitem__)
        return gaps[edge] / dpi, edge

    text_mask = np.zeros((height, width), dtype=bool)
    for box in boxes:
        text_mask[box.y0 : box.y1, box.x0 : box.x1] = True

    elements: list[Element] = []

    # Text first, one element per detected line. Its pixels are then removed so glyphs are
    # not also reported as a scatter of tiny blobs.
    for box in boxes:
        region = arr[box.y0 : box.y1, box.x0 : box.x1]
        ink = keys[box.y0 : box.y1, box.x0 : box.x1] != background_key
        colour = region[ink].mean(axis=0) if ink.any() else background
        x_in, y_in = to_trim(box.x0, box.y0)
        clear_in, edge = clearance(box.x0, box.y0, box.width_px, box.height_px)
        elements.append(
            Element(
                id=f"T{len(elements) + 1}",
                shape="text",
                colour=_hex(colour),
                x_in=round(x_in, 3),
                y_in=round(y_in, 3),
                w_in=round(box.width_px / dpi, 3),
                h_in=round(box.height_px / dpi, 3),
                clearance_in=round(clear_in, 3),
                nearest_edge=edge,  # type: ignore[arg-type]
                spans_edge=False,
                height_pt=round(box.height_pt(dpi), 2),
                px=(box.x0, box.y0, box.x1, box.y1),
            )
        )

    # Everything else: one element per connected region of one colour.
    for key in np.flatnonzero(counts >= MIN_ELEMENT_PX).tolist():
        if key == background_key:
            continue
        mask = ((keys == key) & ~text_mask).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for idx in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[idx])
            if area < MIN_ELEMENT_PX:
                continue
            component = labels[y : y + h, x : x + w] == idx
            colour = arr[y : y + h, x : x + w][component].mean(axis=0)
            fill = area / float(w * h)
            long_side, short_side = max(w, h), max(1, min(w, h))
            thickness_pt: float | None = None
            if fill >= SOLID_FILL and long_side / short_side >= LINE_ASPECT:
                shape: Shape = "line"
                thickness_pt = round(short_side / dpi * 72.0, 3)
            elif fill >= SOLID_FILL:
                shape = "rect"
            elif _is_outline(component):
                shape = "outline"
                thickness_pt = round(_median_thickness(component) / dpi * 72.0, 3)
            else:
                shape = "blob"
            spans = (w >= 0.9 * width and (y == 0 or y + h == height)) or (
                h >= 0.9 * height and (x == 0 or x + w == width)
            )
            rings = None
            if shape in ("outline", "blob"):
                rings = tuple(
                    tuple(
                        (round(tx, 3), round(ty, 3))
                        for tx, ty in (to_trim(px + x, py + y) for px, py in ring)
                    )
                    for ring in _rings(component)
                )
            x_in, y_in = to_trim(x, y)
            clear_in, edge = clearance(x, y, w, h)
            elements.append(
                Element(
                    id=f"E{len(elements) + 1}",
                    shape=shape,
                    colour=_hex(colour),
                    x_in=round(x_in, 3),
                    y_in=round(y_in, 3),
                    w_in=round(w / dpi, 3),
                    h_in=round(h / dpi, 3),
                    clearance_in=round(clear_in, 3),
                    nearest_edge=edge,  # type: ignore[arg-type]
                    spans_edge=bool(spans),
                    thickness_pt=thickness_pt,
                    rings=rings,
                    px=(x, y, x + w, y + h),
                )
            )

    findings = tuple(
        {
            "code": i.code.value,
            "severity": i.severity.value,
            "measured": i.evidence.measured,
            "required": i.evidence.required,
            "unit": i.evidence.unit,
        }
        for i in issues
    )
    return SceneDocument(
        product=spec.display_name,
        trim_in=(order.width_in, order.height_in),
        bleed_in=round(bleed_x / dpi, 4),
        safe_zone_in=spec.safe_zone_in,
        dpi=round(dpi, 2),
        background=_hex(background),
        elements=tuple(elements),
        findings=findings,
    )


def _rings(component: np.ndarray) -> list[list[tuple[int, int]]]:
    """Outer boundaries and holes of a component as simplified polygons, outer rings first."""
    contours, hierarchy = cv2.findContours(
        component.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if hierarchy is None:
        return []
    outer: list[list[tuple[int, int]]] = []
    holes: list[list[tuple[int, int]]] = []
    for contour, (_next, _prev, _child, parent) in zip(contours, hierarchy[0], strict=True):
        simple = cv2.approxPolyDP(contour, POLYGON_EPSILON_PX, closed=True)
        ring = [(int(p[0][0]), int(p[0][1])) for p in simple]
        if len(ring) >= 3:
            (holes if parent >= 0 else outer).append(ring)
    return outer + holes


def _is_outline(component: np.ndarray) -> bool:
    """A closed stroke: filling its holes adds a lot of area."""
    mask = component.astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return int(filled.sum()) > 2 * int(mask.sum())


def _median_thickness(component: np.ndarray) -> float:
    """Median stroke thickness: twice the distance-transform value along the stroke centre."""
    dist = cv2.distanceTransform(component.astype(np.uint8), cv2.DIST_L2, 3)
    ridge = dist[dist >= np.maximum(1.0, dist.max() * 0.5)]
    return float(2 * np.median(ridge)) if ridge.size else 1.0


# --------------------------------------------------------------------------------------
# Render-and-compare
# --------------------------------------------------------------------------------------


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def render_scene(scene: SceneDocument, size: tuple[int, int]) -> np.ndarray:
    """Redraw the artwork from the description alone.

    Uses only what a model would be given — shape, colour, box, thickness — plus the pixel
    box that the inch coordinates were computed from. Text is drawn as a solid box because
    the description carries a text line's extent and size, not its glyphs.
    """
    width, height = size
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = _rgb(scene.background)
    bleed_px = scene.bleed_in * scene.dpi
    bleed_y = (height - scene.trim_in[1] * scene.dpi) / 2

    def to_px(ring: tuple[tuple[float, float], ...]) -> np.ndarray:
        return np.array(
            [[round(x * scene.dpi + bleed_px), round(y * scene.dpi + bleed_y)] for x, y in ring],
            dtype=np.int32,
        )

    for e in scene.elements:
        colour = _rgb(e.colour)
        if e.rings:
            outer = [to_px(r) for r in e.rings if _signed_area(r) != 0]
            # outer rings were emitted first; holes follow and are cut back to background.
            n_outer = _outer_count(e.rings)
            cv2.fillPoly(canvas, outer[:n_outer], colour)
            if outer[n_outer:]:
                cv2.fillPoly(canvas, outer[n_outer:], _rgb(scene.background))
        else:
            x0, y0, x1, y1 = e.px
            canvas[y0:y1, x0:x1] = colour
    return canvas


def _signed_area(ring: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True)
    )


def _outer_count(rings: tuple[tuple[tuple[float, float], ...], ...]) -> int:
    """OpenCV traces outer boundaries and holes in opposite directions; count the leading run."""
    if not rings:
        return 0
    first = _signed_area(rings[0]) > 0
    n = 0
    for ring in rings:
        if (_signed_area(ring) > 0) != first:
            break
        n += 1
    return n


def fidelity(image: Image.Image, scene: SceneDocument, exclude_text: bool = True) -> float:
    """How much of the artwork's ink the description reproduces, as intersection-over-union.

    Ink is anything that is not the background colour. Text boxes are excluded by default:
    the description deliberately reports text as a line with a size, and a box drawn in
    place of glyphs would be scored as a large error that says nothing about whether the
    *non-text* layout was captured. 1.0 means the redraw puts ink exactly where the artwork
    does; a model reasoning from this description is reasoning about the real layout.
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = arr.shape[:2]
    redraw = render_scene(scene, (width, height))
    bg = np.array(_rgb(scene.background), dtype=np.int16)
    real = np.abs(arr.astype(np.int16) - bg).max(axis=2) > 24
    drawn = np.abs(redraw.astype(np.int16) - bg).max(axis=2) > 24
    if exclude_text:
        keep = np.ones((height, width), dtype=bool)
        for e in scene.elements:
            if e.shape == "text":
                x0, y0, x1, y1 = e.px
                keep[y0:y1, x0:x1] = False
        real &= keep
        drawn &= keep
    union = int((real | drawn).sum())
    if union == 0:
        return 1.0
    return round(int((real & drawn).sum()) / union, 4)


def describe(
    image: Image.Image,
    spec: ProductSpec,
    order: OrderMetadata,
    dpi: float,
    boxes: list[TextBox],
    issues: list[Issue],
) -> SceneDocument:
    """Extract the scene and stamp it with its own fidelity score."""
    scene = extract_scene(image, spec, order, dpi, boxes, issues)
    return scene.model_copy(update={"fidelity": fidelity(image, scene)})


__all__ = [
    "Element",
    "SceneDocument",
    "describe",
    "extract_scene",
    "fidelity",
    "render_scene",
]
