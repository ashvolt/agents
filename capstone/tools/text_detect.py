"""Text detection — the interface, plus a dependency-free implementation.

research.md D-3: `TEXT_TOO_SMALL` is arithmetic *once you know where the text is*. Finding
it is a detection problem, and a purpose-built detector beats a vision-language model at
it — on CPU, for free, and returning identical boxes every run, which SC-008 requires.

Which production detector (PaddleOCR / Tesseract / CRAFT) is OQ-3, decided by measured
recall at small point sizes. The interface below is what that decision plugs into:

    detect_text(image) -> list[TextBox]

`ConnectedComponentDetector` is the offline default. It is a real algorithm — run-length
connected components, then horizontal line grouping — not a stub that peeks at the
generator. It is deliberately simple and its known weaknesses are documented on the class,
because a detector that quietly under-reports turns TEXT_TOO_SMALL into a silent false
approve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from PIL import Image

# A component must be at least this many pixels to count as a glyph rather than noise.
MIN_COMPONENT_PIXELS = 4

# Glyphs on one line are grouped when their vertical centres sit within this fraction of
# the taller glyph's height.
LINE_ALIGNMENT_TOLERANCE = 0.6

# A text line needs at least this many glyph-like components. Two blobs side by side are
# more likely to be artwork than writing.
MIN_GLYPHS_PER_LINE = 3

# Components wider or taller than this fraction of the image are structural artwork
# (borders, panels, the main shape), not letters.
MAX_GLYPH_EXTENT_RATIO = 0.35


@dataclass(frozen=True)
class TextBox:
    """One detected line of text, in pixel coordinates."""

    x0: int
    y0: int
    x1: int
    y1: int
    glyph_count: int

    @property
    def height_px(self) -> int:
        return self.y1 - self.y0

    @property
    def width_px(self) -> int:
        return self.x1 - self.x0

    @property
    def region(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def height_pt(self, dpi: float) -> float:
        """Cap height in points at the given resolution. The number the spec compares to."""
        return self.height_px / dpi * 72.0


class TextDetector(Protocol):
    """Swap-in point for OQ-3. Any implementation must be deterministic."""

    def detect(self, image: Image.Image) -> list[TextBox]: ...


# --------------------------------------------------------------------------------------
# Connected components, run-length based
# --------------------------------------------------------------------------------------


def ink_mask(
    image: Image.Image, threshold_ratio: float = 0.55, cap: int | None = None
) -> np.ndarray:
    """Boolean mask of pixels that differ from the dominant (background) luminance.

    Threshold is relative to the observed spread rather than absolute, so it survives a
    low-contrast file — which matters, because LOW_CONTRAST and TEXT_TOO_SMALL can occur
    on the same file and one must not blind the other.
    """
    grey = np.asarray(image.convert("L"), dtype=np.int16)
    if grey.size == 0:
        return np.zeros((0, 0), dtype=bool)

    counts = np.bincount(grey.ravel(), minlength=256)
    background = int(counts.argmax())
    deviation = np.abs(grey - background)
    spread = int(deviation.max())
    if spread < 8:  # effectively a flat image; nothing to detect
        return np.zeros_like(grey, dtype=bool)
    threshold = max(6, spread * threshold_ratio)
    if cap is not None:
        # Never demand more than `cap` levels of difference. A relative threshold alone
        # hides mid-tone elements whenever the file also holds near-black ink: on real
        # artwork with dark outlines, a mid-grey hairline rule fell under 55% of the
        # spread and was never measured (real-art.md).
        threshold = min(threshold, max(6, cap))
    return deviation >= threshold


def _label_runs(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Connected components as (x0, y0, x1, y1) boxes, via run-length union-find.

    Row runs are extracted vectorised, then runs in adjacent rows that overlap
    horizontally are unioned. Linear in the number of runs, which is small even for large
    images — this is why it does not need scipy.
    """
    if mask.size == 0:
        return []

    height, width = mask.shape
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    runs: list[tuple[int, int, int, int]] = []  # (row, start, end_exclusive, run_id)
    prev_row_runs: list[tuple[int, int, int]] = []  # (start, end, run_id)

    for y in range(height):
        row = mask[y]
        if not row.any():
            prev_row_runs = []
            continue
        # Boundaries where the run state flips.
        padded = np.concatenate(([False], row, [False]))
        diff = np.diff(padded.astype(np.int8))
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)

        current: list[tuple[int, int, int]] = []
        for s, e in zip(starts.tolist(), ends.tolist(), strict=True):
            run_id = len(parent)
            parent.append(run_id)
            runs.append((y, s, e, run_id))
            current.append((s, e, run_id))
            for ps, pe, pid in prev_row_runs:
                if s < pe and ps < e:  # horizontal overlap -> same component
                    union(run_id, pid)
        prev_row_runs = current

    boxes: dict[int, list[int]] = {}
    for y, s, e, run_id in runs:
        root = find(run_id)
        box = boxes.get(root)
        if box is None:
            boxes[root] = [s, y, e, y + 1]
        else:
            box[0] = min(box[0], s)
            box[1] = min(box[1], y)
            box[2] = max(box[2], e)
            box[3] = max(box[3], y + 1)

    return [(b[0], b[1], b[2], b[3]) for b in boxes.values()]


class ConnectedComponentDetector:
    """Group glyph-sized connected components into horizontal text lines.

    **Known weaknesses**, stated because an under-reporting detector turns
    TEXT_TOO_SMALL into a silent false approve:

    - Touching or heavily kerned glyphs merge into one component, lowering the glyph
      count below MIN_GLYPHS_PER_LINE and hiding a short line entirely.
    - Text on a busy background merges with the background structure.
    - Rotated or curved text is not grouped, because grouping assumes a horizontal line.
    - A single very short word (< 3 glyphs) is discarded as artwork.

    Each of those fails *toward missing text*, so the residual risk lands on
    TEXT_TOO_SMALL recall. That is measured on the eval set rather than assumed away, and
    it is the primary reason OQ-3 exists.
    """

    name = "connected-components"

    def detect(self, image: Image.Image) -> list[TextBox]:
        mask = ink_mask(image)
        if mask.size == 0:
            return []
        height, width = mask.shape
        max_w = width * MAX_GLYPH_EXTENT_RATIO
        max_h = height * MAX_GLYPH_EXTENT_RATIO

        glyphs: list[tuple[int, int, int, int]] = []
        for x0, y0, x1, y1 in _label_runs(mask):
            w, h = x1 - x0, y1 - y0
            if w * h < MIN_COMPONENT_PIXELS:
                continue
            if w > max_w or h > max_h:
                continue  # structural artwork, not a letter
            if h == 0 or w / h > 8:
                continue  # a rule or a border, not a glyph
            glyphs.append((x0, y0, x1, y1))

        return _group_into_lines(glyphs)


def _group_into_lines(glyphs: list[tuple[int, int, int, int]]) -> list[TextBox]:
    """Cluster glyph boxes that share a baseline into line boxes."""
    if not glyphs:
        return []

    remaining = sorted(glyphs, key=lambda b: (b[1], b[0]))
    lines: list[list[tuple[int, int, int, int]]] = []

    for box in remaining:
        cy = (box[1] + box[3]) / 2
        h = box[3] - box[1]
        placed = False
        for line in lines:
            ref = line[0]
            ref_cy = (ref[1] + ref[3]) / 2
            ref_h = ref[3] - ref[1]
            tolerance = max(h, ref_h) * LINE_ALIGNMENT_TOLERANCE
            if abs(cy - ref_cy) <= tolerance:
                line.append(box)
                placed = True
                break
        if not placed:
            lines.append([box])

    out: list[TextBox] = []
    for line in lines:
        if len(line) < MIN_GLYPHS_PER_LINE:
            continue
        x0 = min(b[0] for b in line)
        y0 = min(b[1] for b in line)
        x1 = max(b[2] for b in line)
        y1 = max(b[3] for b in line)
        out.append(TextBox(x0=x0, y0=y0, x1=x1, y1=y1, glyph_count=len(line)))
    return out


class DBNetDetector:
    """PaddleOCR's PP-OCRv3 text detector (DBNet), run on CPU through ONNX Runtime.

    OQ-3/OQ-4, decided by measurement on real artwork (real-art.md): the connected-
    component detector found no text at all on most real captions — antialiased type on
    coloured backgrounds — and the no-text guard then escalated 70 clean files out of 270.

    **Detection only.** RapidOCR bundles the model in its wheel (2.4 MB, no download). Its
    recogniser is never called and no text content leaves this class, so the rest of the
    pipeline still cannot carry what the artwork *says* to a language model (narrate.py).

    **Boxes are tightened to the ink.** DBNet pads its boxes (unclip ratio ~1.6) so they
    contain the glyphs comfortably. Measuring text size from a padded box would report
    type as larger than it is — the dangerous direction for TEXT_TOO_SMALL — so each box
    is shrunk to the rows and columns that actually differ from the box's own background.
    """

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # optional dependency, imported lazily

        self._detect = RapidOCR().text_detector

    def detect(self, image: Image.Image) -> list[TextBox]:
        rgb = np.asarray(image.convert("RGB"))
        if rgb.size == 0:
            return []
        quads, _elapsed = self._detect(np.ascontiguousarray(rgb[:, :, ::-1]))
        height, width = rgb.shape[:2]
        boxes: list[TextBox] = []
        for quad in [] if quads is None else quads:
            xs, ys = quad[:, 0], quad[:, 1]
            x0, x1 = max(0, int(xs.min())), min(width, int(np.ceil(xs.max())))
            y0, y1 = max(0, int(ys.min())), min(height, int(np.ceil(ys.max())))
            tight = _tighten(rgb[y0:y1, x0:x1])
            if tight is None:
                continue
            tx0, ty0, tx1, ty1, glyphs = tight
            boxes.append(TextBox(x0 + tx0, y0 + ty0, x0 + tx1, y0 + ty1, glyphs))
        return sorted(boxes, key=lambda b: (b.y0, b.x0))


def _tighten(region: np.ndarray) -> tuple[int, int, int, int, int] | None:
    """Ink extent inside a padded box, plus a glyph count. None if the box holds no ink."""
    if region.size == 0:
        return None
    flat = region.reshape(-1, 3)
    keys = ((flat >> 4).astype(np.int32) * np.array([256, 16, 1], dtype=np.int32)).sum(axis=1)
    background = flat[keys == int(np.bincount(keys).argmax())].mean(axis=0)
    ink = np.abs(region.astype(np.int16) - background.astype(np.int16)).max(axis=2) > 24
    # The padded box can also catch a rule or an edge of artwork above or below the line.
    # Split the ink into horizontal bands separated by empty rows and keep the band with
    # the most ink: that is the text line; a thin rule under it is not.
    row_ink = ink.sum(axis=1)
    bands: list[tuple[int, int]] = []
    start = None
    for y, has_ink in enumerate(row_ink > 0):
        if has_ink and start is None:
            start = y
        elif not has_ink and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, len(row_ink)))
    if not bands:
        return None
    by0, by1 = max(bands, key=lambda b: int(row_ink[b[0] : b[1]].sum()))
    line = ink[by0:by1]
    cols = np.flatnonzero(line.any(axis=0))
    return int(cols[0]), by0, int(cols[-1]) + 1, by1, len(_label_runs(line))


def _default_detector() -> TextDetector:
    """DBNet when it is installed, unless PREFLIGHT_TEXT_DETECTOR=components says otherwise.

    The fallback exists so the package still runs without the optional dependency, not as
    an equal alternative: on real artwork the component detector is not fit for purpose.
    """
    import os

    if os.environ.get("PREFLIGHT_TEXT_DETECTOR", "dbnet") == "components":
        return ConnectedComponentDetector()
    try:
        return DBNetDetector()
    except ImportError:
        return ConnectedComponentDetector()


_DEFAULT: TextDetector | None = None


def detect_text(image: Image.Image, detector: TextDetector | None = None) -> list[TextBox]:
    """Locate text lines. Deterministic."""
    global _DEFAULT
    if detector is None:
        if _DEFAULT is None:
            _DEFAULT = _default_detector()
        detector = _DEFAULT
    return detector.detect(image)


__all__ = [
    "ConnectedComponentDetector",
    "DBNetDetector",
    "TextBox",
    "TextDetector",
    "detect_text",
    "ink_mask",
]
