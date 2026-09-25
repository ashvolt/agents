"""Bucket 2 — checks computable from pixels. Exact, offline, ~free.

research.md D-1: these four were originally filed under "judgement". They are measurement
problems. Stroke width is a run-length minimum; contrast is a deltaE; transparency is the
alpha channel; text size is arithmetic once a detector has located the text.

Moving them out of the model is the single largest cost and latency saving in the design,
and it is what makes the `--no-tools` control arm measure something worth measuring.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

from capstone.src.schemas import Evidence, Issue, IssueCode, ProductSpec, Severity
from capstone.tools.text_detect import TextBox, _label_runs, detect_text, ink_mask

PT_PER_INCH = 72.0

# Alpha below this counts as transparent rather than a rounding artefact.
ALPHA_OPAQUE_FLOOR = 250

# Fraction of pixels that must be transparent before it is worth reporting. A handful of
# stray pixels is an export artefact; a visible hole is not.
TRANSPARENCY_REPORT_RATIO = 0.005

# A connected component must hold at least this share of the file's ink before its
# thickness counts. Below it we are measuring speckle.
COMPONENT_MIN_INK_RATIO = 0.002

# ...unless it is at least this long at print size. The ratio above was tuned on sparse
# synthetic art; next to a dense real illustration a 1.5 in hairline rule holds under
# 0.2% of the ink and was skipped as speckle (real-art.md). Length is what makes a line.
STROKE_MIN_LENGTH_IN = 0.08

# Text boxes are excluded from the stroke measurement with this much margin, as a share
# of the line's height. Without it the antialiased fringe just outside a tight box was
# measured as a one-pixel stroke: 22 of 26 false THIN_LINES flags on real art.
TEXT_EXCLUSION_PAD = 0.15

# Colour regions for the stroke measurement. A quantised colour is a real colour of the
# artwork if it covers at least this share of the canvas (or REGION_MIN_PX pixels);
# everything else is antialiasing and is reassigned to the nearest real colour. Real
# colours closer than REGION_MERGE_DELTA_E are one region, so the 16-level steps of a
# gradient do not become a stack of thin bands. The merge threshold sits below every
# product's minimum contrast (15), so two colours a customer can tell apart on press
# are never merged.
REGION_MIN_SHARE = 0.0005
REGION_MIN_PX = 30
REGION_MERGE_DELTA_E = 10.0

# The brightness mask never demands more than this much luminance difference from the
# background, however dark the darkest ink in the file is. See `ink_mask`.
STROKE_MASK_CAP = 24

# A colour-region component counts as a free-standing stroke when at least this share of
# the pixels just around it are background. Details inside an illustration (a highlight
# in a face) are surrounded by other ink and are not what a line-weight rule is for:
# measuring every region took real-art auto-approval from 78% to 32% (real-art.md).
FREE_STANDING_BG_SHARE = 0.8

# Faint-text search: the image's difference from its background is multiplied by this
# before a second detection pass. A line that only appears after amplification is, by
# definition, low-contrast text.
FAINT_TEXT_GAIN = 4.0

# Opposing-edge ink asymmetry above which the keep-out margin is called suspicious.
#
# Tuned on the 312-case TRAIN split only, by sweeping it against the false-approve rate:
#   0.06 -> 83.2% approve / 3.1% false-approve   (5 wrong)
#   0.03 -> 77.9% approve / 2.0% false-approve   (3 wrong)
#   0.02 -> 70.0% approve / 0.7% false-approve   (1 wrong)
# Paired with the no-text gate below, 0.03 gives 76.3% / 0.7% - the highest approve rate
# that still clears SC-002. Lower values keep buying precision at roughly 8 points of
# approve rate each, which is the SC-001/SC-002 trade stated explicitly.
#
# Fitted to synthetic data where every intrusion is one block on one edge, so it raises an
# ADVISORY finding and escalates rather than telling a customer their file is wrong.
SAFE_ZONE_ASYMMETRY_THRESHOLD = 0.03


# --------------------------------------------------------------------------------------
# Transparency
# --------------------------------------------------------------------------------------


def check_transparency(image: Image.Image, spec: ProductSpec) -> list[Issue]:
    if spec.allows_transparency:
        return []
    if image.mode not in ("RGBA", "LA", "PA"):
        return []

    alpha = np.asarray(image.convert("RGBA").split()[-1], dtype=np.uint8)
    if alpha.size == 0:
        return []
    transparent = alpha < ALPHA_OPAQUE_FLOOR
    ratio = float(transparent.mean())
    if ratio < TRANSPARENCY_REPORT_RATIO:
        return []

    ys, xs = np.nonzero(transparent)
    region = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))

    return [
        Issue(
            code=IssueCode.UNINTENDED_TRANSPARENCY,
            severity=Severity.BLOCKING,
            message=(
                f"{ratio:.1%} of the artwork is transparent, but {spec.display_name} is "
                "printed on an opaque substrate. Transparent areas will print as the "
                "bare material, which is usually not what was intended."
            ),
            evidence=Evidence(
                measured=round(ratio * 100, 2),
                required=round(TRANSPARENCY_REPORT_RATIO * 100, 2),
                unit="% transparent",
                region=region,
            ),
        )
    ]


# --------------------------------------------------------------------------------------
# Contrast
# --------------------------------------------------------------------------------------


def _srgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    def inv_gamma(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (inv_gamma(float(v)) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e76(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, aa, ba = _srgb_to_lab(a)
    lb, ab, bb = _srgb_to_lab(b)
    return math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


# A colour cluster must cover at least this fraction of the image to count as a design
# element. Below it, we are looking at antialiasing fringes, not something the customer
# drew.
ELEMENT_MIN_AREA_RATIO = 0.002

# Clusters this close to the background are the background, fragmented by quantisation.
SAME_COLOUR_DELTA_E = 2.0


def measure_contrast(
    image: Image.Image,
) -> tuple[float, tuple[int, int, int], tuple[int, int, int]] | None:
    """deltaE between the background and the *weakest* design element on it.

    Deliberately the weakest rather than the strongest: a file with one bold element and
    one nearly-invisible one should fail, and taking the maximum hides exactly that case.

    Element discovery does not go through `ink_mask`, which was the original bug here.
    That mask thresholds relative to the largest deviation present, so a file containing
    both a bold accent and a faint element classifies the faint one as background and
    reports perfect contrast — a false approve on precisely the case the check exists for.
    Clusters are enumerated directly from the colour histogram instead.
    """
    rgb = image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.uint8)
    if arr.size == 0:
        return None

    height, width = arr.shape[:2]
    flat = arr.reshape(-1, 3)
    total = flat.shape[0]

    # Quantise to 16 levels per channel so antialiasing does not fragment the histogram.
    keys = ((flat >> 4).astype(np.int32) * np.array([256, 16, 1], dtype=np.int32)).sum(axis=1)
    counts = np.bincount(keys)
    if counts.size == 0:
        return None

    background_key = int(counts.argmax())
    background = tuple(float(c) for c in flat[keys == background_key].mean(axis=0))

    # deltaE once per colour cluster (at most 4096), then broadcast back to pixels.
    present = np.flatnonzero(counts > 0)
    de_by_key = np.zeros(counts.size, dtype=np.float32)
    for key in present.tolist():
        colour = tuple(float(c) for c in flat[keys == key].mean(axis=0))
        de_by_key[key] = delta_e76(background, colour)

    # Elements are *spatially connected regions*, not colour bins. This is what stops
    # antialiasing halos being reported as faint elements: a halo is a one-pixel fringe
    # attached to the shape it surrounds, so it merges into that component and the
    # component's mean colour is dominated by the solid core. Scoring colour bins
    # independently instead produced a false LOW_CONTRAST on almost every clean file.
    de_map = de_by_key[keys].reshape(height, width)
    element_mask = de_map >= SAME_COLOUR_DELTA_E
    if not element_mask.any():
        return None

    min_pixels = max(1, int(total * ELEMENT_MIN_AREA_RATIO))
    weakest: tuple[float, tuple[int, int, int]] | None = None

    for x0, y0, x1, y1 in _label_runs(element_mask):
        sub = element_mask[y0:y1, x0:x1]
        if int(sub.sum()) < min_pixels:
            continue
        pixels = arr[y0:y1, x0:x1][sub]
        # The element's colour is its solid core, as for text lines: pixels within 80% of
        # its strongest deviation (a 95th percentile, so one noisy pixel cannot define
        # it). A plain mean let JPEG chroma subsampling, which smears a thin coloured rule
        # into the background, read a clearly visible line as a faint one: 36 of 47 wrong
        # rejections on the customer-mistake set (mistakes_v1, re-saved and chat-app
        # JPEGs). A genuinely faint element is uniform, so its core is its mean.
        de_pixels = de_map[y0:y1, x0:x1][sub]
        core = pixels[de_pixels >= 0.8 * float(np.percentile(de_pixels, 95))]
        colour = tuple(float(c) for c in (core if core.size else pixels).mean(axis=0))
        de = delta_e76(background, colour)
        if weakest is None or de < weakest[0]:
            weakest = (de, tuple(int(round(c)) for c in colour))  # type: ignore[arg-type]

    if weakest is None:
        return None
    bg = tuple(int(round(c)) for c in background)
    return (weakest[0], bg, weakest[1])  # type: ignore[return-value]


def measure_text_contrast(
    image: Image.Image, boxes: list[TextBox]
) -> tuple[float, tuple[int, int, int], tuple[int, int, int]] | None:
    """deltaE between each text line and the background immediately around it; the worst.

    `measure_contrast` finds elements as connected regions and skips regions below a share
    of the canvas. A caption is many small regions — one per letter — and every one fell
    under that floor, so a low-contrast caption beside a large illustration was never
    measured (7 of 17 false approves on real art). Text lines are measured as lines.

    The ink colour is the mean of the line's solid core — pixels within 80% of its
    strongest deviation — so antialiased edges do not drag it toward the background (a
    median cut read a grey-222 caption on white as deltaE 3.8; its true value is ~10.7).
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width = arr.shape[:2]
    worst: tuple[float, tuple[int, int, int], tuple[int, int, int]] | None = None
    for box in boxes:
        pad = max(2, box.height_px // 2)
        around = arr[max(0, box.y0 - pad) : box.y1 + pad, max(0, box.x0 - pad) : box.x1 + pad]
        flat = around.reshape(-1, 3)
        keys = ((flat >> 4) * np.array([256, 16, 1])).sum(axis=1)
        background = flat[keys == int(np.bincount(keys).argmax())].mean(axis=0)
        inside = arr[box.y0 : box.y1, box.x0 : box.x1].reshape(-1, 3)
        if inside.size == 0:
            continue
        deviation = np.abs(inside - background).max(axis=1)
        if deviation.max() < 8:
            continue
        core = inside[deviation >= 0.8 * deviation.max()]
        ink = core.mean(axis=0)
        bg_t = tuple(int(round(c)) for c in background)
        ink_t = tuple(int(round(c)) for c in ink)
        de = delta_e76(bg_t, ink_t)  # type: ignore[arg-type]
        if worst is None or de < worst[0]:
            worst = (de, bg_t, ink_t)  # type: ignore[assignment]
    return worst


def find_faint_text(image: Image.Image, known: list[TextBox]) -> list[TextBox]:
    """Text lines visible only after amplifying the image's difference from its background.

    A caption pale enough to be a contrast defect can also be too pale for the detector,
    and if the detector found *anything else* — part of an illustration — the no-text guard
    stays quiet and the caption is never measured (5 of 14 remaining false approves on
    real art). Amplify, detect again, and keep the lines the first pass missed; the
    contrast check then measures them on the original pixels.
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    if arr.size == 0:
        return []
    flat = arr.reshape(-1, 3).astype(np.int32)
    keys = ((flat >> 4) * np.array([256, 16, 1])).sum(axis=1)
    background = flat[keys == int(np.bincount(keys).argmax())].mean(axis=0)
    boosted = np.clip(background + FAINT_TEXT_GAIN * (arr - background), 0, 255).astype(np.uint8)
    found = detect_text(Image.fromarray(boosted))

    def overlaps(a: TextBox, b: TextBox) -> bool:
        return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1

    return [box for box in found if not any(overlaps(box, k) for k in known)]


def check_contrast(
    image: Image.Image, spec: ProductSpec, boxes: list[TextBox] | None = None
) -> list[Issue]:
    candidates = [
        m
        for m in (measure_contrast(image), measure_text_contrast(image, boxes or []))
        if m is not None
    ]
    if not candidates:
        return []
    delta_e, background, ink = min(candidates, key=lambda m: m[0])
    if delta_e >= spec.min_contrast_delta_e:
        return []
    return [
        Issue(
            code=IssueCode.LOW_CONTRAST,
            severity=Severity.BLOCKING,
            message=(
                f"Some elements sit only deltaE {delta_e:.1f} from the background; "
                f"{spec.display_name} needs at least {spec.min_contrast_delta_e:g}. "
                "They will be hard to see on the printed piece."
            ),
            evidence=Evidence(
                measured=round(delta_e, 2),
                required=spec.min_contrast_delta_e,
                unit="deltaE",
                note=f"background rgb{background} vs element rgb{ink}",
            ),
        )
    ]


# --------------------------------------------------------------------------------------
# Stroke width
# --------------------------------------------------------------------------------------


def _run_lengths_along_axis(mask: np.ndarray, axis: int) -> np.ndarray:
    """For every True pixel, the length of the contiguous run it belongs to along `axis`."""
    if axis == 0:
        mask = mask.T
    out = np.zeros(mask.shape, dtype=np.int32)
    for i in range(mask.shape[0]):
        row = mask[i]
        if not row.any():
            continue
        padded = np.concatenate(([False], row, [False]))
        diff = np.diff(padded.astype(np.int8))
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)
        for s, e in zip(starts.tolist(), ends.tolist(), strict=True):
            out[i, s:e] = e - s
    return out.T if axis == 0 else out


def measure_min_stroke_px(
    image: Image.Image, exclude: list[TextBox] | None = None, dpi: float | None = None
) -> float | None:
    """Thinnest stroke present, in pixels.

    Measured **per connected component**, taking each component's median thickness and
    then the minimum across components. A global percentile — the original implementation
    — does not work: a hairline occupying 1% of the ink is invisible at the 5th
    percentile when a solid panel supplies the rest, so the thinnest element in the file
    is exactly the one that gets averaged away.

    The median *within* a component resists antialiased edges; the minimum *across*
    components is what the spec actually asks about.

    Text is excluded because it has its own check (TEXT_TOO_SMALL). Letterforms are
    legitimately thinner than the artwork minimum at small sizes, and counting them here
    would flag every file carrying small type as a thin-line defect.
    """
    candidates = [
        v
        for v in (
            _luminance_min_stroke(image, exclude, dpi),
            _free_standing_min_stroke(image, exclude, dpi),
        )
        if v is not None
    ]
    return min(candidates) if candidates else None


def _excluded(shape: tuple[int, ...], exclude: list[TextBox] | None) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=bool)
    for box in exclude or []:
        pad = max(2, round(TEXT_EXCLUSION_PAD * box.height_px))
        mask[max(0, box.y0 - pad) : box.y1 + pad, max(0, box.x0 - pad) : box.x1 + pad] = True
    return mask


def _luminance_min_stroke(
    image: Image.Image, exclude: list[TextBox] | None, dpi: float | None
) -> float | None:
    """Thinnest stroke on a brightness mask: every ink element, embedded details included."""
    mask = ink_mask(image, cap=STROKE_MASK_CAP)
    if mask.size == 0 or not mask.any():
        return None
    mask &= ~_excluded(mask.shape, exclude)
    if not mask.any():
        return None

    thickness = np.minimum(
        _run_lengths_along_axis(mask, axis=1), _run_lengths_along_axis(mask, axis=0)
    )
    total_ink = int(mask.sum())
    min_component_px = max(4, int(total_ink * COMPONENT_MIN_INK_RATIO))
    min_length_px = STROKE_MIN_LENGTH_IN * dpi if dpi else float("inf")

    thinnest: float | None = None
    for x0, y0, x1, y1 in _label_runs(mask):
        sub_mask = mask[y0:y1, x0:x1]
        area = int(sub_mask.sum())
        if area < min_component_px and max(x1 - x0, y1 - y0) < min_length_px:
            continue
        values = thickness[y0:y1, x0:x1][sub_mask]
        if values.size == 0:
            continue
        median = float(np.median(values))
        if median > 0 and (thinnest is None or median < thinnest):
            thinnest = median

    if thinnest is None:
        # Everything was below the component floor; fall back to the global minimum so a
        # file made entirely of hairlines is not silently reported as clean.
        values = thickness[mask]
        thinnest = float(values.min()) if values.size else None
    return thinnest


def _free_standing_min_stroke(
    image: Image.Image, exclude: list[TextBox] | None, dpi: float | None
) -> float | None:
    """Thinnest free-standing stroke, measured on colour regions.

    Catches what brightness cannot see - a grey rule on navy differs in colour, barely
    in luminance - while ignoring details embedded in other ink.
    """
    regions, background = colour_regions(image)
    if regions.size == 0:
        return None
    excluded = _excluded(regions.shape, exclude)
    ink = (regions != background) & ~excluded
    total_ink = int(ink.sum())
    if total_ink == 0:
        return None
    min_component_px = max(4, int(total_ink * COMPONENT_MIN_INK_RATIO))
    min_length_px = STROKE_MIN_LENGTH_IN * dpi if dpi else float("inf")
    height, width = regions.shape
    ring_kernel = np.ones((3, 3), np.uint8)

    thinnest: float | None = None
    for region in np.unique(regions[ink]).tolist():
        mask = (regions == region) & ~excluded
        thickness = np.minimum(
            _run_lengths_along_axis(mask, axis=1), _run_lengths_along_axis(mask, axis=0)
        )
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8
        )
        for idx in range(1, count):
            x0, y0, w, h, area = (int(v) for v in stats[idx])
            if area < min_component_px and max(w, h) < min_length_px:
                continue
            ya, yb = max(0, y0 - 1), min(height, y0 + h + 1)
            xa, xb = max(0, x0 - 1), min(width, x0 + w + 1)
            component = labels[ya:yb, xa:xb] == idx
            ring = cv2.dilate(component.astype(np.uint8), ring_kernel).astype(bool) & ~component
            around = regions[ya:yb, xa:xb][ring]
            if around.size == 0 or (around == background).mean() < FREE_STANDING_BG_SHARE:
                continue
            values = thickness[ya:yb, xa:xb][component]
            median = float(np.median(values)) if values.size else 0.0
            if median > 0 and (thinnest is None or median < thinnest):
                thinnest = median
    return thinnest


def colour_regions(image: Image.Image) -> tuple[np.ndarray, int]:
    """Each pixel's colour region, and the background region's id.

    The artwork's real colours are the quantised colours that cover a meaningful area.
    Every other pixel — the antialiased edge between two real colours — takes the nearest
    real colour, and real colours within REGION_MERGE_DELTA_E of each other are joined,
    so a gradient is one region rather than a stack of bands.
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if arr.size == 0:
        return np.zeros((0, 0), dtype=np.int32), 0
    flat = arr.reshape(-1, 3)
    keys = ((flat >> 4).astype(np.int32) * np.array([256, 16, 1], dtype=np.int32)).sum(axis=1)
    counts = np.bincount(keys, minlength=4096)
    present = np.flatnonzero(counts)
    sums = np.stack(
        [np.bincount(keys, weights=flat[:, c], minlength=4096) for c in range(3)], axis=1
    )
    means = sums[present] / counts[present][:, None]

    floor = max(REGION_MIN_PX, int(flat.shape[0] * REGION_MIN_SHARE))
    major = present[counts[present] >= floor]
    if major.size == 0:
        major = present[[int(np.argmax(counts[present]))]]
    major_means = sums[major] / counts[major][:, None]

    # join real colours closer than the merge threshold (union-find over the palette)
    parent = list(range(len(major)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    labs = [
        _srgb_to_lab(tuple(float(c) for c in m))  # type: ignore[arg-type]
        for m in major_means
    ]
    for i in range(len(major)):
        for j in range(i + 1, len(major)):
            if math.dist(labs[i], labs[j]) < REGION_MERGE_DELTA_E:
                parent[find(i)] = find(j)

    # every present quantised colour -> nearest real colour -> its region
    nearest = np.argmin(((means[:, None, :] - major_means[None, :, :]) ** 2).sum(axis=2), axis=1)
    lut = np.zeros(4096, dtype=np.int32)
    lut[present] = np.array([find(int(n)) for n in nearest], dtype=np.int32)
    regions = lut[keys].reshape(arr.shape[:2])
    background = int(np.bincount(regions.ravel()).argmax())
    return regions, background


def check_stroke_width(
    image: Image.Image, spec: ProductSpec, dpi: float, exclude: list[TextBox] | None = None
) -> list[Issue]:
    """Thinnest stroke against the spec minimum.

    Known limitation: at low DPI a sub-pixel stroke is rasterised to one pixel, so 1.1x
    and 2.0x past the limit become indistinguishable. Physically real — a press cannot
    print half a pixel either — but it means per-magnitude recall flattens at the bottom
    end, and that is reported rather than smoothed over.
    """
    if dpi <= 0:
        return []
    min_px = measure_min_stroke_px(image, exclude=exclude, dpi=dpi)
    if min_px is None or min_px <= 0:
        return []

    min_pt = min_px / dpi * PT_PER_INCH
    if min_pt >= spec.min_stroke_pt:
        return []
    return [
        Issue(
            code=IssueCode.THIN_LINES,
            severity=Severity.BLOCKING,
            message=(
                f"The thinnest lines measure about {min_pt:.2f} pt; {spec.display_name} "
                f"needs {spec.min_stroke_pt:g} pt or heavier. Thinner strokes break up or "
                "disappear on press."
            ),
            evidence=Evidence(measured=round(min_pt, 3), required=spec.min_stroke_pt, unit="pt"),
        )
    ]


# --------------------------------------------------------------------------------------
# Text size
# --------------------------------------------------------------------------------------


def check_text_size(boxes: list[TextBox], spec: ProductSpec, dpi: float) -> list[Issue]:
    """Smallest detected text line against the spec minimum.

    Absence of detected text is NOT evidence of absence — see the documented weaknesses on
    ConnectedComponentDetector. An empty box list produces no issue here, and the agent
    treats "no text found" as a fact about the detector rather than a fact about the file.
    """
    if not boxes or dpi <= 0:
        return []

    smallest = min(boxes, key=lambda b: b.height_px)
    pt = smallest.height_pt(dpi)
    if pt >= spec.min_text_pt:
        return []
    return [
        Issue(
            code=IssueCode.TEXT_TOO_SMALL,
            severity=Severity.BLOCKING,
            message=(
                f"The smallest text measures about {pt:.1f} pt; {spec.display_name} needs "
                f"at least {spec.min_text_pt:g} pt. Smaller type fills in and becomes "
                "unreadable."
            ),
            evidence=Evidence(
                measured=round(pt, 2),
                required=spec.min_text_pt,
                unit="pt",
                region=smallest.region,
            ),
        )
    ]


# --------------------------------------------------------------------------------------
# The bucket
# --------------------------------------------------------------------------------------


def measure_safe_zone(image: Image.Image, spec: ProductSpec, dpi: float) -> dict[str, object]:
    """Describe ink inside the keep-out margin. Evidence for bucket 3, not a verdict.

    Whether ink sits inside the cut margin is a bounding-box test and therefore
    computable. Whether that ink is a background deliberately running off the edge or a
    logo about to lose its top third is not — that is the judgement the model is for.

    Measuring it here matters because the model was failing at the *detection* half:
    asked to both find safe-zone intrusions and judge them, it missed 4 of 7 at
    confidence 0.95+. Handing it the measurement leaves only the part it is good at.

    The uniformity signal is the useful one. A background bleeding off the edge covers
    the margin evenly on every side; an intruding element is a dense patch on one side.
    """
    safe_px = spec.safe_zone_in * dpi
    mask = ink_mask(image)
    if mask.size == 0 or safe_px < 1:
        return {"measurable": False, "reason": "no ink or safe zone smaller than a pixel"}

    height, width = mask.shape
    band = max(1, int(round(safe_px)))
    if height <= 2 * band or width <= 2 * band:
        return {"measurable": False, "reason": "safe zone larger than the artwork"}

    edges = {
        "top": mask[:band, :],
        "bottom": mask[-band:, :],
        "left": mask[:, :band],
        "right": mask[:, -band:],
    }
    coverage = {name: round(float(region.mean()), 4) for name, region in edges.items()}

    # Asymmetry between OPPOSING edges is the signal, not overall spread. Artwork that
    # deliberately bleeds off the edge runs off both sides equally; an element intruding
    # into the margin lands on one side only. Comparing all four edges against each other
    # does not work, because a design with a banner across the top saturates top/bottom
    # while leaving left/right low, and that is perfectly correct artwork.
    horizontal = abs(coverage["left"] - coverage["right"])
    vertical = abs(coverage["top"] - coverage["bottom"])
    asymmetry = round(max(horizontal, vertical), 4)

    interior = mask[band:-band, band:-band]
    interior_coverage = round(float(interior.mean()), 4) if interior.size else 0.0

    # Positive evidence only. The first version of this reported "symmetric - consistent
    # with artwork bleeding off the edge by design" below the threshold, and safe-zone
    # recall COLLAPSED from 43% to 14%: the model read a confident-sounding all-clear and
    # stopped looking. The measurement can show that something is wrong. It cannot show
    # that nothing is. Same rule already documented on the text detector, broken here.
    if asymmetry >= SAFE_ZONE_ASYMMETRY_THRESHOLD:
        reading = (
            "ASYMMETRIC - one edge carries markedly more ink than the edge opposite it. "
            "That pattern is typical of something intruding into the margin rather than "
            "artwork bleeding off it."
        )
    else:
        reading = (
            "Below the asymmetry threshold. This measurement has NOT found evidence of an "
            "intrusion, which is not the same as finding evidence there is none. It misses "
            "roughly a third of genuine intrusions - anything centred, symmetric, or "
            "spread across opposing edges is invisible to it. Judge from the image."
        )

    return {
        "measurable": True,
        "safe_zone_px": band,
        "edge_ink_coverage": coverage,
        "interior_ink_coverage": interior_coverage,
        "opposing_edge_asymmetry": asymmetry,
        "threshold": SAFE_ZONE_ASYMMETRY_THRESHOLD,
        "reading": reading,
        "calibration": (
            "Measured over 95 cases: clean files never exceeded 0.059, and two thirds of "
            "genuine intrusions sat above 0.06, so 0.06 catches 67% of them with no false "
            "positives. The remaining third sit below it and are invisible to this "
            "measurement. NOTE: calibrated on synthetic artwork where every intrusion is a "
            "single block on one edge; it will not transfer unchanged to real files."
        ),
        "interpretation_hint": (
            "These numbers say WHERE ink sits relative to the cut margin. They do not say "
            "whether it matters. A background running off the edge is correct and "
            "expected; a logo or text about to lose part of itself is not. Look at the "
            "image to decide which one this is."
        ),
    }


def check_safe_zone(image: Image.Image, spec: ProductSpec, dpi: float) -> list[Issue]:
    """Raise an ADVISORY finding when the margin is measurably asymmetric.

    Advisory rather than blocking, and therefore an escalation rather than a fix request:
    the threshold is fitted to synthetic artwork (research.md D-4), so it is good enough
    to demand a human look and not good enough to tell a customer their file is wrong.

    At 0.06 this fired on zero clean files across the evaluation set, so the added human
    load is small and the failure direction is the cheap one.
    """
    measurement = measure_safe_zone(image, spec, dpi)
    if not measurement.get("measurable"):
        return []
    asymmetry = float(measurement["opposing_edge_asymmetry"])  # type: ignore[arg-type]
    if asymmetry < SAFE_ZONE_ASYMMETRY_THRESHOLD:
        return []

    coverage = measurement["edge_ink_coverage"]
    return [
        Issue(
            code=IssueCode.CONTENT_IN_SAFE_ZONE,
            severity=Severity.ADVISORY,
            message=(
                "Ink coverage inside the keep-out margin is uneven between opposite edges, "
                "which often means artwork intrudes where the blade cuts. A human should "
                "confirm whether this is deliberate."
            ),
            evidence=Evidence(
                measured=asymmetry,
                required=SAFE_ZONE_ASYMMETRY_THRESHOLD,
                unit="edge asymmetry",
                note=f"edge ink coverage {coverage}",
            ),
        )
    ]


def check_text_detection_inconclusive(boxes: list[TextBox], spec: ProductSpec) -> list[Issue]:
    """Escalate when the detector found no text at all.

    The detector's documented failure modes - merged glyphs, lines under three glyphs,
    busy backgrounds, rotation - all fail toward finding NOTHING. So an empty result is
    ambiguous between "this file has no text" and "this file has text I could not see",
    and only one of those is safe to approve.

    Measured on the train split: two of the five remaining false approves were files with
    text at twice the minimum size that the detector missed entirely. Turning its own
    blind spot into an escalation removes both, and costs about 2 points of approve rate.

    This is the same rule the tool payload already states and that a previous version of
    the safe-zone reading broke: absence of evidence is not evidence of absence.
    """
    if boxes:
        return []
    return [
        Issue(
            code=IssueCode.TEXT_TOO_SMALL,
            severity=Severity.ADVISORY,
            message=(
                "No text was detected in this artwork. The detector misses small, merged, "
                "or rotated type, so this cannot be read as confirmation that the file "
                "carries no text below the minimum size. A human should confirm."
            ),
            evidence=Evidence(
                note=(
                    "text detector returned zero lines; minimum size for "
                    f"{spec.display_name} is {spec.min_text_pt:g} pt"
                )
            ),
        )
    ]


# A checkerboard painted into the pixels: two light, neutral greys alternating in square
# cells. AI image tools draw it when asked for "transparent background"; it prints as a
# grid of grey squares. Neutral = channels within this spread; light = at least this bright.
CHECKER_NEUTRAL_SPREAD = 14
CHECKER_MIN_LUMA = 150
CHECKER_LEVEL_GAP = (10, 90)  # the two greys differ by this much, in 0-255 luma
CHECKER_CELL_PX = (4, 64)  # cell sizes searched, on the analysis-sized image
CHECKER_MIN_AGREEMENT = 0.95
CHECKER_MIN_COVERAGE = 0.10  # share of the image the pattern must cover
# The first version accepted 85% agreement over as few as 16 sampled cells, and fired on
# 16 of 1,100 Stable Diffusion logos on textured grey backdrops, none a checkerboard
# (ai-art.md section 5). Two painted greys are two separate peaks with a valley between
# them; paper texture is one broad hump. And a real grid alternates over many cells.
CHECKER_MIN_CELLS = 64  # sampled cells at a known level: an 8 x 8 grid at least
CHECKER_MAX_VALLEY = 0.5  # histogram between the two greys, as a share of the lower peak
CHECKER_ANALYSIS_LONG_SIDE = 768


def measure_checkerboard(image: Image.Image) -> tuple[float, float, int] | None:
    """(coverage, agreement, cell_px) of the strongest checkerboard, or None.

    Samples one pixel per candidate cell and asks whether the light/dark greys alternate
    with (row + column) parity, for every cell size and phase. A real transparency grid
    is regular; a checkered design element (a racing flag, gingham) is dark, coloured or
    small, and fails one of the neutral, light or coverage tests.
    """
    rgb = image.convert("RGB")
    scale = min(1.0, CHECKER_ANALYSIS_LONG_SIDE / max(rgb.size))
    if scale < 1.0:
        rgb = rgb.resize(
            (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.NEAREST
        )
    arr = np.asarray(rgb, dtype=np.int16)
    if arr.size == 0:
        return None
    spread = arr.max(axis=2) - arr.min(axis=2)
    luma = arr.mean(axis=2)
    neutral = (spread <= CHECKER_NEUTRAL_SPREAD) & (luma >= CHECKER_MIN_LUMA)
    if neutral.mean() < CHECKER_MIN_COVERAGE:
        return None

    hist = np.bincount(np.clip(luma[neutral], 0, 255).astype(np.int32), minlength=256)
    first = int(hist.argmax())
    far = hist.copy()
    far[max(0, first - CHECKER_LEVEL_GAP[0] + 1) : first + CHECKER_LEVEL_GAP[0]] = 0
    second = int(far.argmax())
    gap = abs(first - second)
    if not CHECKER_LEVEL_GAP[0] <= gap <= CHECKER_LEVEL_GAP[1] or hist[second] < 0.2 * hist[first]:
        return None
    smooth = np.convolve(hist, np.ones(3) / 3, mode="same")
    lo, hi = sorted((first, second))
    valley = smooth[lo + 1 : hi].min() if hi - lo > 1 else 0.0
    if valley > CHECKER_MAX_VALLEY * min(smooth[first], smooth[second]):
        return None
    tolerance = max(3, gap // 3)
    level = np.full(luma.shape, -1, dtype=np.int8)
    level[neutral & (np.abs(luma - first) <= tolerance)] = 0
    level[neutral & (np.abs(luma - second) <= tolerance)] = 1

    height, width = level.shape
    best: tuple[float, float, int] | None = None
    for cell in range(CHECKER_CELL_PX[0], CHECKER_CELL_PX[1] + 1):
        rows, cols = height // cell, width // cell
        if rows < 4 or cols < 4:
            break
        parity = (np.arange(rows)[:, None] + np.arange(cols)[None, :]) % 2
        for oy in range(0, cell, max(1, cell // 4)):
            for ox in range(0, cell, max(1, cell // 4)):
                ys = oy + cell * np.arange(rows) + cell // 2
                xs = ox + cell * np.arange(cols) + cell // 2
                ys, xs = ys[ys < height], xs[xs < width]
                samples = level[np.ix_(ys, xs)]
                known = samples >= 0
                if known.sum() < CHECKER_MIN_CELLS:
                    continue
                par = parity[: samples.shape[0], : samples.shape[1]]
                match = (samples == par) & known
                agree = max(match.sum(), (known & ~match).sum()) / known.sum()
                ones = (samples[known] == 1).mean()
                if not 0.3 <= ones <= 0.7:
                    continue
                coverage = known.sum() * agree / samples.size
                if agree >= CHECKER_MIN_AGREEMENT and (best is None or coverage > best[0]):
                    best = (float(coverage), float(agree), int(round(cell / scale)))
    return best


def check_fake_transparency(image: Image.Image) -> list[Issue]:
    found = measure_checkerboard(image)
    if found is None or found[0] < CHECKER_MIN_COVERAGE:
        return []
    coverage, agreement, cell = found
    return [
        Issue(
            code=IssueCode.FAKE_TRANSPARENCY,
            severity=Severity.BLOCKING,
            message=(
                "The background is a grey-and-white checkerboard drawn into the image, not "
                "real transparency. It will print as a grid of grey squares. Export with a "
                "transparent or solid background instead."
            ),
            evidence=Evidence(
                measured=round(coverage * 100, 1),
                required=round(CHECKER_MIN_COVERAGE * 100, 1),
                unit="% of the image",
                note=f"checkerboard cells ~{cell}px, {agreement:.0%} of sampled cells alternate",
            ),
        )
    ]


def analyse_pixels(
    image: Image.Image, spec: ProductSpec, dpi: float
) -> tuple[list[Issue], list[TextBox]]:
    """Run every bucket-2 check. Returns issues and the text boxes for downstream use."""
    boxes = detect_text(image)
    issues: list[Issue] = []
    issues += check_transparency(image, spec)
    issues += check_fake_transparency(image)
    issues += check_contrast(image, spec, boxes + find_faint_text(image, boxes))
    issues += check_stroke_width(image, spec, dpi, exclude=boxes)
    issues += check_text_size(boxes, spec, dpi)
    issues += check_safe_zone(image, spec, dpi)
    issues += check_text_detection_inconclusive(boxes, spec)
    return issues, boxes


BUCKET2_CHECKS = (
    "check_transparency",
    "check_fake_transparency",
    "SAFE_ZONE_ASYMMETRY_THRESHOLD",
    "check_contrast",
    "check_safe_zone",
    "check_text_detection_inconclusive",
    "check_stroke_width",
    "check_text_size",
)

__all__ = [
    "BUCKET2_CHECKS",
    "check_fake_transparency",
    "measure_checkerboard",
    "analyse_pixels",
    "SAFE_ZONE_ASYMMETRY_THRESHOLD",
    "check_contrast",
    "check_safe_zone",
    "check_text_detection_inconclusive",
    "check_stroke_width",
    "check_text_size",
    "check_transparency",
    "delta_e76",
    "measure_contrast",
    "measure_min_stroke_px",
    "measure_text_contrast",
    "colour_regions",
    "find_faint_text",
    "measure_safe_zone",
]
