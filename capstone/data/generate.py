"""Synthetic artwork generator.

The critical property (FR-018): **defects are injected, so gold labels are correct by
construction.** A file rendered at half the required resolution is labelled
LOW_RESOLUTION because this module made it that way, not because somebody eyeballed it.
No hand-labelling, no annotator disagreement.

The second critical property (FR-019): **perturbations straddle the threshold.** Each
one takes a `magnitude` — severity relative to the spec limit, where >1.0 is past it.
Generating at {0.5, 0.9, 1.1, 2.0} puts two cases just inside the limit and two just
outside, so the eval measures the band where false approves actually come from rather
than only the obvious cases. See research.md D-4 for why a generator that only emits
easy cases is grading a ruler against itself.

Honest limitation, repeated from brief.md S9: this generator and the checks in
capstone/tools/ share my assumptions about what each defect *is*. Straddling thresholds
catches boundary and unit bugs; it does not cure that shared-assumption risk, and
synthetic art is far simpler than real customer uploads.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from capstone.src.product_specs import all_product_ids, get_spec
from capstone.src.schemas import (
    GoldLabel,
    IssueCode,
    OrderMetadata,
    Perturbation,
    ProductSpec,
    Split,
)

# Magnitudes: two inside the limit, two outside. 0.9 and 1.1 are the pair that matter.
STRADDLE = (0.5, 0.9, 1.1, 2.0)

# Binary defects have no meaningful gradient — you either saved RGB or you did not.
BINARY_MAGNITUDE = 2.0

PT_PER_INCH = 72.0

# Codes this generator can produce. UNREADABLE_FILE and the bucket-3 codes are handled
# separately: the first by corrupting bytes, the others by placing content deliberately.
GRADED_CODES = (
    IssueCode.LOW_RESOLUTION,
    IssueCode.MISSING_BLEED,
    IssueCode.ASPECT_MISMATCH,
    IssueCode.THIN_LINES,
    IssueCode.LOW_CONTRAST,
    IssueCode.TEXT_TOO_SMALL,
    IssueCode.CONTENT_IN_SAFE_ZONE,
)
BINARY_CODES = (
    IssueCode.WRONG_COLOR_MODE,
    IssueCode.UNINTENDED_TRANSPARENCY,
    IssueCode.UNREADABLE_FILE,
)


# --------------------------------------------------------------------------------------
# Plan for one case — decided before any pixel is drawn, so the label is the plan
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CasePlan:
    """Everything the renderer needs, and exactly what the gold label records."""

    case_id: str
    spec: ProductSpec
    order: OrderMetadata
    perturbations: tuple[Perturbation, ...]
    split: Split
    seed: int
    # Which edge a CONTENT_IN_SAFE_ZONE mark intrudes from. Always "left" in every dataset
    # generated before 2026-09-23; `--intrusion-edges any` varies it so a check that has
    # quietly learned "intrusions are on the left" gets caught.
    intrusion_edge: str = "left"

    def magnitude_for(self, code: IssueCode) -> float:
        """1.0 means 'exactly at the limit'; absent means 'comfortably inside'."""
        for p in self.perturbations:
            if p.code == code:
                return p.magnitude
        return 0.0  # no perturbation requested

    def has(self, code: IssueCode) -> bool:
        return any(p.code == code for p in self.perturbations)


# --------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------


def required_canvas_inches(spec: ProductSpec, order: OrderMetadata) -> tuple[float, float]:
    """Physical size the file must cover: trim plus bleed on every edge."""
    return (
        order.width_in + 2 * spec.bleed_in,
        order.height_in + 2 * spec.bleed_in,
    )


def _pt_to_px(pt: float, dpi: float) -> float:
    return pt / PT_PER_INCH * dpi


# --------------------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------------------


def _srgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB -> CIELAB (D65). Used to build pairs at a target deltaE."""

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


def delta_e76(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, aa, ba = _srgb_to_lab(a)
    lb, ab, bb = _srgb_to_lab(b)
    return math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


def foreground_at_delta_e(
    background: tuple[int, int, int], target_delta_e: float
) -> tuple[int, int, int]:
    """A grey whose deltaE from `background` is as close to the target as we can get.

    Binary search on lightness. Approximate by design — the eval cares that the value
    lands on the intended side of the threshold, and the checker measures the real
    deltaE rather than trusting this.
    """
    lo, hi = 0, 255
    best, best_err = (0, 0, 0), float("inf")
    for _ in range(24):
        mid = (lo + hi) // 2
        cand = (mid, mid, mid)
        de = delta_e76(background, cand)
        err = abs(de - target_delta_e)
        if err < best_err:
            best, best_err = cand, err
        if de < target_delta_e:
            # move further from the background
            if mid < background[0]:
                hi = mid - 1
            else:
                lo = mid + 1
        else:
            if mid < background[0]:
                lo = mid + 1
            else:
                hi = mid - 1
        if lo > hi:
            break
    return best


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------

BACKGROUND = (242, 240, 236)


def render(plan: CasePlan) -> tuple[Image.Image, float]:
    """Draw one case. Returns the image and the DPI to embed.

    Every perturbation is applied here by changing *how* things are drawn, never by
    post-processing a finished clean image. That keeps the defect intrinsic to the file
    rather than an artefact sitting on top of one.
    """
    spec, order = plan.spec, plan.order
    rng = random.Random(plan.seed)

    canvas_w_in, canvas_h_in = required_canvas_inches(spec, order)

    # --- LOW_RESOLUTION -------------------------------------------------------------
    # A customer scaling a small image up to the ordered size: fewer pixels AND the
    # metadata DPI drops with them, so physical size stays correct and only sharpness
    # suffers. Keeping physical size right is what stops this tripping MISSING_BLEED.
    dpi = float(spec.min_dpi)
    if plan.has(IssueCode.LOW_RESOLUTION):
        dpi = spec.min_dpi / plan.magnitude_for(IssueCode.LOW_RESOLUTION)

    # --- MISSING_BLEED --------------------------------------------------------------
    # Bleed shrinks while DPI stays honest, so the file is simply too small physically.
    bleed_in = spec.bleed_in
    if plan.has(IssueCode.MISSING_BLEED):
        bleed_in = spec.bleed_in / plan.magnitude_for(IssueCode.MISSING_BLEED)
        canvas_w_in = order.width_in + 2 * bleed_in
        canvas_h_in = order.height_in + 2 * bleed_in

    px_w = max(8, round(canvas_w_in * dpi))
    px_h = max(8, round(canvas_h_in * dpi))

    # --- ASPECT_MISMATCH ------------------------------------------------------------
    if plan.has(IssueCode.ASPECT_MISMATCH):
        deviation = spec.aspect_tolerance * plan.magnitude_for(IssueCode.ASPECT_MISMATCH)
        px_w = max(8, round(px_w * (1.0 + deviation)))

    img = Image.new("RGB", (px_w, px_h), BACKGROUND)
    draw = ImageDraw.Draw(img)

    bleed_px = bleed_in * dpi
    safe_px = spec.safe_zone_in * dpi
    trim = (bleed_px, bleed_px, px_w - bleed_px, px_h - bleed_px)
    safe = (trim[0] + safe_px, trim[1] + safe_px, trim[2] - safe_px, trim[3] - safe_px)

    # --- LOW_CONTRAST ---------------------------------------------------------------
    target_de = spec.min_contrast_delta_e
    if plan.has(IssueCode.LOW_CONTRAST):
        target_de = spec.min_contrast_delta_e / plan.magnitude_for(IssueCode.LOW_CONTRAST)
    else:
        target_de = spec.min_contrast_delta_e * 2.5  # comfortably legible
    ink = foreground_at_delta_e(BACKGROUND, target_de)

    # Bleed region gets colour so it is visibly intentional artwork, not white space.
    draw.rectangle((0, 0, px_w, px_h), fill=BACKGROUND)
    accent = foreground_at_delta_e(BACKGROUND, spec.min_contrast_delta_e * 3)
    draw.rectangle((0, 0, px_w, int(bleed_px * 1.5) or 1), fill=accent)
    draw.rectangle((0, px_h - (int(bleed_px * 1.5) or 1), px_w, px_h), fill=accent)

    # --- main shape, inside the safe zone -------------------------------------------
    sx0, sy0, sx1, sy1 = safe
    if sx1 - sx0 > 10 and sy1 - sy0 > 10:
        pad_x = (sx1 - sx0) * 0.12
        pad_y = (sy1 - sy0) * 0.28
        draw.ellipse(
            (sx0 + pad_x, sy0 + pad_y, sx1 - pad_x, sy1 - pad_y),
            outline=ink,
            width=max(2, int(_pt_to_px(spec.min_stroke_pt * 3, dpi))),
        )

    # --- THIN_LINES -----------------------------------------------------------------
    stroke_pt = spec.min_stroke_pt
    if plan.has(IssueCode.THIN_LINES):
        stroke_pt = spec.min_stroke_pt / plan.magnitude_for(IssueCode.THIN_LINES)
    else:
        stroke_pt = spec.min_stroke_pt * 2.0
    stroke_px = max(1, round(_pt_to_px(stroke_pt, dpi)))

    line_y = sy0 + (sy1 - sy0) * 0.18
    if sx1 - sx0 > 20:
        for i in range(3):
            y = line_y + i * max(2, stroke_px * 3)
            draw.line((sx0 + 6, y, sx1 - 6, y), fill=ink, width=stroke_px)

    # --- TEXT_TOO_SMALL -------------------------------------------------------------
    text_pt = spec.min_text_pt
    if plan.has(IssueCode.TEXT_TOO_SMALL):
        text_pt = spec.min_text_pt / plan.magnitude_for(IssueCode.TEXT_TOO_SMALL)
    else:
        text_pt = spec.min_text_pt * 1.8
    text_px = max(4, int(round(_pt_to_px(text_pt, dpi))))

    try:
        font = ImageFont.load_default(size=text_px)
    except Exception:  # pragma: no cover - very old Pillow
        font = ImageFont.load_default()

    label = rng.choice(["FRESH BATCH", "SINCE 2014", "HANDMADE", "LIMITED RUN"])
    ty = sy1 - (sy1 - sy0) * 0.22
    if sx1 - sx0 > 20:
        draw.text((sx0 + 8, ty), label, font=font, fill=ink)

    # --- CONTENT_IN_SAFE_ZONE -------------------------------------------------------
    # Bucket 3. A solid mark intruding past the keep-out margin toward the blade.
    if plan.has(IssueCode.CONTENT_IN_SAFE_ZONE):
        intrusion = safe_px * plan.magnitude_for(IssueCode.CONTENT_IN_SAFE_ZONE)
        mark_w = max(6, int(safe_px * 1.5) or 6)
        mid_y = (sy0 + (sy1 - sy0) * 0.4, sy0 + (sy1 - sy0) * 0.6)
        mid_x = (sx0 + (sx1 - sx0) * 0.4, sx0 + (sx1 - sx0) * 0.6)
        if plan.intrusion_edge == "right":
            x1 = trim[2] - safe_px + intrusion
            box = (x1 - mark_w, mid_y[0], x1, mid_y[1])
        elif plan.intrusion_edge == "top":
            y0 = trim[1] + safe_px - intrusion
            box = (mid_x[0], y0, mid_x[1], y0 + mark_w)
        elif plan.intrusion_edge == "bottom":
            y1 = trim[3] - safe_px + intrusion
            box = (mid_x[0], y1 - mark_w, mid_x[1], y1)
        else:  # "left" - the original placement, unchanged
            x0 = trim[0] + safe_px - intrusion
            box = (x0, mid_y[0], x0 + mark_w, mid_y[1])
        draw.rectangle(box, fill=accent)

    return img, dpi


# --------------------------------------------------------------------------------------
# Saving — colour mode and transparency are decided at write time
# --------------------------------------------------------------------------------------


def save_case(img: Image.Image, dpi: float, plan: CasePlan, out_dir: Path) -> Path:
    """Write the file and return its path.

    Colour mode and transparency are properties of the saved file rather than of the
    drawing, so they are applied here.

    Note: injecting transparency implies an RGBA PNG, and every product that disallows
    transparency also requires CMYK — so a transparency case is necessarily also a
    colour-mode case. That is realistic (customers upload RGBA PNGs constantly) and the
    plan records both perturbations rather than pretending one of them is absent.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    if plan.has(IssueCode.UNINTENDED_TRANSPARENCY):
        rgba = img.convert("RGBA")
        px = rgba.load()
        w, h = rgba.size
        # Punch a few holes the customer almost certainly did not intend.
        for cx, cy in ((w // 4, h // 3), (w // 2, h // 2)):
            r = max(3, min(w, h) // 12)
            for y in range(max(0, cy - r), min(h, cy + r)):
                for x in range(max(0, cx - r), min(w, cx + r)):
                    if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                        px[x, y] = (px[x, y][0], px[x, y][1], px[x, y][2], 0)
        path = out_dir / f"{plan.case_id}.png"
        rgba.save(path, dpi=(dpi, dpi))

    elif plan.has(IssueCode.WRONG_COLOR_MODE):
        path = out_dir / f"{plan.case_id}.png"
        img.convert("RGB").save(path, dpi=(dpi, dpi))

    else:
        path = out_dir / f"{plan.case_id}.tif"
        img.convert("CMYK").save(path, dpi=(dpi, dpi))

    if plan.has(IssueCode.UNREADABLE_FILE):
        # Truncate to half. Decodes as a corrupt file, which is the point.
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])

    return path


# --------------------------------------------------------------------------------------
# Planning the set
# --------------------------------------------------------------------------------------

_ORDER_SIZES = ((2.0, 2.0), (3.0, 3.0), (4.0, 2.0), (5.0, 3.0), (2.5, 3.5))


def _make_order(rng: random.Random, idx: int, product_id: str) -> OrderMetadata:
    w, h = rng.choice(_ORDER_SIZES)
    return OrderMetadata(
        order_id=f"ORD-{idx:05d}",
        product_id=product_id,
        width_in=w,
        height_in=h,
        quantity=rng.choice((50, 100, 250, 500, 1000)),
    )


def plan_cases(
    n: int,
    seed: int = 20260922,
    holdout_fraction: float = 0.25,
    clean_fraction: float = 0.40,
) -> list[CasePlan]:
    """Build the case list.

    Default mix follows brief.md S9: ~40% clean, ~40% single-perturbation, ~20% multi.
    "Clean" includes files perturbed to 0.5x and 0.9x of a limit — inside spec but
    deliberately close to it — because those are the files a nervous agent wrongly
    rejects, and false rejects are a tracked metric.

    `clean_fraction` raises the share of clean cases, and it exists for a statistical
    reason rather than a realism one. SC-002 is false approves divided by *approvals*,
    and approvals come almost entirely from clean files, so the clean count sets the
    resolution of the headline metric: 65 clean cases can only resolve ~2.2%, which
    cannot test a 1% limit. Raising it to ~440 gets ~330 approvals and a resolution near
    0.3%. See limits.md S7.

    The defective cases carry per-issue recall, and that signal is already adequate at
    the current count — so the cheapest way to make SC-002 measurable is to add clean
    cases, not to scale everything.
    """
    rng = random.Random(seed)
    products = all_product_ids()
    plans: list[CasePlan] = []

    clean_fraction = min(max(clean_fraction, 0.05), 0.95)
    defective_fraction = 1.0 - clean_fraction

    # Clean splits evenly between pristine files and near-misses sitting just inside a
    # limit; defective splits 2:1 between single-defect and multi-defect.
    n_pristine = round(n * clean_fraction * 0.5)
    n_near_miss = round(n * clean_fraction) - n_pristine
    n_single = round(n * defective_fraction * (2 / 3))
    n_multi = n - n_pristine - n_near_miss - n_single

    def new_plan(idx: int, perts: tuple[Perturbation, ...]) -> CasePlan:
        product_id = products[idx % len(products)]
        spec = get_spec(product_id)
        order = _make_order(rng, idx, product_id)
        split = Split.HOLDOUT if rng.random() < holdout_fraction else Split.TRAIN
        return CasePlan(
            case_id=f"case-{idx:05d}",
            spec=spec,
            order=order,
            perturbations=perts,
            split=split,
            seed=seed + idx,
        )

    idx = 0

    # Pristine: nothing touched.
    for _ in range(n_pristine):
        plans.append(new_plan(idx, ()))
        idx += 1

    # Near miss: inside the limit, deliberately close to it. Still clean.
    for _ in range(n_near_miss):
        code = rng.choice(GRADED_CODES)
        magnitude = rng.choice((0.5, 0.9))
        plans.append(new_plan(idx, (Perturbation(code=code, magnitude=magnitude),)))
        idx += 1

    # Single defect: past the limit, half of them only just.
    for _ in range(n_single):
        if rng.random() < 0.25:
            code = rng.choice(BINARY_CODES)
            perts = (Perturbation(code=code, magnitude=BINARY_MAGNITUDE),)
            if code is IssueCode.UNINTENDED_TRANSPARENCY:
                # Implied by the file format; recorded rather than hidden.
                perts = perts + (
                    Perturbation(code=IssueCode.WRONG_COLOR_MODE, magnitude=BINARY_MAGNITUDE),
                )
        else:
            code = rng.choice(GRADED_CODES)
            perts = (Perturbation(code=code, magnitude=rng.choice((1.1, 2.0))),)
        plans.append(new_plan(idx, perts))
        idx += 1

    # Multi: two or three at once, at least one past the limit.
    for _ in range(n_multi):
        k = rng.choice((2, 2, 3))
        codes = rng.sample(GRADED_CODES, k)
        perts = tuple(
            Perturbation(code=c, magnitude=rng.choice(STRADDLE)) for c in codes
        )
        if not any(p.is_defect for p in perts):
            perts = (Perturbation(code=codes[0], magnitude=1.1),) + perts[1:]
        plans.append(new_plan(idx, perts))
        idx += 1

    rng.shuffle(plans)
    return plans


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def generate(
    n: int = 200,
    seed: int = 20260922,
    out_dir: Path | None = None,
    manifest: Path | None = None,
    clean_fraction: float = 0.40,
    intrusion_edges: str = "left",
) -> Path:
    """Render the dataset and write the manifest. Returns the manifest path.

    `intrusion_edges="any"` picks the safe-zone intrusion edge per case from the case's own
    seed, after planning, so every other property of every case is identical to the
    `"left"` dataset with the same seed. Only the mark moves.
    """
    root = Path(__file__).resolve().parent
    out_dir = out_dir or root / "cases"
    manifest = manifest or root / "cases.jsonl"

    plans = plan_cases(n, seed=seed, clean_fraction=clean_fraction)
    if intrusion_edges == "any":
        edges = ("left", "right", "top", "bottom")
        plans = [replace(p, intrusion_edge=random.Random(p.seed).choice(edges)) for p in plans]
    rows: list[dict] = []

    for plan in plans:
        img, dpi = render(plan)
        path = save_case(img, dpi, plan, out_dir)
        label = GoldLabel(
            case_id=plan.case_id,
            perturbations=plan.perturbations,
            split=plan.split,
        )
        rows.append(
            {
                "case_id": plan.case_id,
                "image_path": str(path.relative_to(root.parent.parent)).replace("\\", "/"),
                "order": plan.order.model_dump(),
                "label": label.model_dump(mode="json"),
                "rendered_dpi": round(dpi, 3),
            }
        )
        if plan.intrusion_edge != "left":  # keeps earlier manifests byte-identical
            rows[-1]["intrusion_edge"] = plan.intrusion_edge

    with manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the synthetic preflight dataset.")
    ap.add_argument("-n", type=int, default=200, help="number of cases")
    ap.add_argument("--seed", type=int, default=20260922)
    ap.add_argument(
        "--clean-fraction",
        type=float,
        default=0.40,
        help=(
            "share of cases with no defect. Raise it to make SC-002 measurable: the "
            "false-approve denominator is approvals, which come from clean files."
        ),
    )
    ap.add_argument("--out", type=str, default=None, help="manifest filename")
    ap.add_argument("--cases-dir", type=str, default=None, help="directory for the images")
    ap.add_argument(
        "--intrusion-edges",
        choices=["left", "any"],
        default="left",
        help="where safe-zone intrusions are drawn. 'left' reproduces every earlier dataset",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    manifest = generate(
        n=args.n,
        seed=args.seed,
        clean_fraction=args.clean_fraction,
        manifest=root / args.out if args.out else None,
        out_dir=root / args.cases_dir if args.cases_dir else None,
        intrusion_edges=args.intrusion_edges,
    )

    import collections

    counts: collections.Counter[str] = collections.Counter()
    splits: collections.Counter[str] = collections.Counter()
    clean = 0
    with manifest.open(encoding="utf-8") as fh:
        total = 0
        for line in fh:
            row = json.loads(line)
            total += 1
            label = GoldLabel.model_validate(row["label"])
            splits[label.split] += 1
            if label.is_clean:
                clean += 1
            for p in label.defects:
                counts[p.code] += 1

    print(f"wrote {total} cases -> {manifest}")
    print(f"  clean: {clean} ({clean / total:.0%})   defective: {total - clean}")
    print(f"  split: {dict(splits)}")
    print("  defects by code:")
    for code, n in counts.most_common():
        print(f"    {code:26s} {n}")


if __name__ == "__main__":
    main()
