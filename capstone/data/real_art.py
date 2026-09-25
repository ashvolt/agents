"""Real-artwork evaluation set: professional illustrations laid out as sticker uploads.

    python -m capstone.data.fetch_art                       # once
    python -m capstone.data.real_art -n 450 --seed 20260924

Everything else in this repo was graded on shapes drawn by `generate.py`, which shares
its assumptions with the checks (limits.md §1). This set replaces the drawing with real
design work from three libraries (see `fetch_art.py`) while keeping the labels correct by
construction:

- **Case planning, perturbation magnitudes and file saving are the synthetic generator's
  own** (`plan_cases`, `save_case`), so a label means exactly what it means there.
- **Only the drawing changes.** Each case is a sticker a customer might upload: a
  background (white, or a brand colour running through the bleed), one illustration
  rendered from its vector original at print resolution, a caption in a real typeface,
  and a thin rule under it.
- **Defects are injected into that layout**, never faked on top of it: the illustration
  is placed so its real ink edge crosses the safe line by the planned depth, the caption
  is set at the planned point size or contrast, the rule at the planned stroke.

What cannot be labelled by construction is the illustration's *own* content. An emoji
with a pale highlight is a low-contrast element on a white sticker whether or not the
plan asked for one. Those findings land on files labelled clean, and they are the
interesting part of this set: they are where the rules meet real design. real-art.md
reports them separately instead of pretending the labels cover them.
"""

from __future__ import annotations

import argparse
import io
import json
import random
from pathlib import Path

import cairosvg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from capstone.data.generate import (
    CasePlan,
    _pt_to_px,
    aspect_base_px_w,
    delta_e76,
    foreground_at_delta_e,
    plan_cases,
    required_canvas_inches,
    save_case,
)
from capstone.src.schemas import GoldLabel, IssueCode, Perturbation

ROOT = Path(__file__).resolve().parent
ART = ROOT / "art_cache"
STYLES = ("openmoji", "twemoji", "noto")

WHITE = (255, 255, 255)
# Background colours a small brand might choose. Mid and dark tones on purpose: they are
# where real artwork's own light and dark details lose contrast.
BRAND = ((29, 53, 87), (230, 57, 70), (42, 157, 143), (244, 162, 97), (38, 70, 83))

CAPTIONS = (
    "FRESH ROASTED",
    "HAND POURED",
    "EST. 2019",
    "SMALL BATCH",
    "STAY WEIRD",
    "LOCAL HONEY",
    "GOOD VIBES",
    "HOMEMADE",
    "ORGANIC",
    "LIMITED EDITION",
)


def _art_files() -> dict[str, list[Path]]:
    found = {s: sorted((ART / s).glob("*.svg")) for s in STYLES}
    missing = [s for s, files in found.items() if not files]
    if missing:
        raise SystemExit(f"no artwork for {missing}. Run: python -m capstone.data.fetch_art")
    return found


def _rasterise(svg: Path, size_px: int) -> Image.Image:
    png = cairosvg.svg2png(url=str(svg), output_width=size_px, output_height=size_px)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _ink_box(art: Image.Image) -> tuple[int, int, int, int]:
    alpha = np.asarray(art.split()[-1])
    ys, xs = np.nonzero(alpha > 16)
    if xs.size == 0:
        return 0, 0, art.width, art.height
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


VISIBLE_LEVELS = 24  # the stroke mask's foreground threshold (bucket2_pixels)


def _visible_ink_box(
    art: Image.Image, background: tuple[int, int, int]
) -> tuple[int, int, int, int]:
    """The box of the pixels that will show on this background.

    A raster illustration can keep a margin of near-background colour (an off-white
    field on a white sticker). Placing by the alpha box would count that margin as ink,
    and a planned safe-zone intrusion would cross the line with nothing anyone can see.
    """
    rgba = np.asarray(art).astype(np.int16)
    differs = np.abs(rgba[..., :3] - np.array(background)).max(axis=2) > VISIBLE_LEVELS
    ys, xs = np.nonzero(differs & (rgba[..., 3] > 16))
    if xs.size == 0:
        return _ink_box(art)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def caption_colour(
    background: tuple[int, int, int], target_de: float
) -> tuple[tuple[int, int, int], float]:
    """The shade of the background's own hue whose deltaE from it is closest to target.

    The first two real-art sets used the synthetic generator's helper, which searches
    greys. On a coloured background no grey gets anywhere near a small target - the
    closest grey to navy is ~24 dE away - so seven captions labelled LOW_CONTRAST were
    in fact comfortably legible, and the engine was scored as missing defects that were
    not there (real-art.md). Searching toward black and toward white from the background
    keeps its hue and reaches any target. Returns the colour and the deltaE achieved.
    """
    best: tuple[float, tuple[int, int, int], float] | None = None
    bg = np.array(background, dtype=float)
    for end in (np.zeros(3), np.full(3, 255.0)):
        for step in range(1, 401):
            colour = tuple(int(round(v)) for v in bg + (end - bg) * step / 400)
            de = delta_e76(background, colour)  # type: ignore[arg-type]
            gap = abs(de - target_de)
            if best is None or gap < best[0]:
                best = (gap, colour, de)  # type: ignore[assignment]
    assert best is not None
    return best[1], best[2]


def _fit_raster(art: Image.Image, side: int) -> Image.Image:
    """A raster illustration scaled to fit a side x side box, keeping its aspect."""
    scale = side / max(art.width, art.height)
    size = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
    return art.convert("RGBA").resize(size, Image.Resampling.LANCZOS)


def render_real(
    plan: CasePlan,
    svg: Path | Image.Image,
    style_seed: int,
    oversample: float = 1.0,
    legacy_grey_captions: bool = False,
) -> tuple[Image.Image, float, float]:
    """Lay one illustration out as a sticker upload, applying the plan's perturbations.

    `oversample` renders the identical layout at a multiple of the resolution. It exists
    for adjudication, not for the dataset: the artwork is vector, so a 4x render is the
    ground truth for "is this stroke really thinner than the limit, or did rasterising at
    print resolution make it look that way" (real-art.md).

    `svg` may instead be a raster image (ai_art.py): it is scaled to the same box, and
    its alpha, if any, decides where the ink is. No vector original means no oracle.
    """
    spec, order = plan.spec, plan.order
    rng = random.Random(style_seed)
    canvas_w_in, canvas_h_in = required_canvas_inches(spec, order)

    dpi = float(spec.min_dpi)
    if plan.has(IssueCode.LOW_RESOLUTION):
        dpi = spec.min_dpi / plan.magnitude_for(IssueCode.LOW_RESOLUTION)
    dpi *= oversample

    bleed_in = spec.bleed_in
    if plan.has(IssueCode.MISSING_BLEED):
        bleed_in = spec.bleed_in / plan.magnitude_for(IssueCode.MISSING_BLEED)
        canvas_w_in = order.width_in + 2 * bleed_in
        canvas_h_in = order.height_in + 2 * bleed_in

    px_w = max(8, round(canvas_w_in * dpi))
    px_h = max(8, round(canvas_h_in * dpi))
    if plan.has(IssueCode.ASPECT_MISMATCH):
        base_w = aspect_base_px_w(spec, order, bleed_in, px_w, px_h)
        deviation = spec.aspect_tolerance * plan.magnitude_for(IssueCode.ASPECT_MISMATCH)
        px_w = max(8, round(base_w * (1.0 + deviation)))

    background = WHITE if rng.random() < 0.6 else rng.choice(BRAND)
    img = Image.new("RGB", (px_w, px_h), background)
    draw = ImageDraw.Draw(img)

    bleed_px, safe_px = bleed_in * dpi, spec.safe_zone_in * dpi
    sx0, sy0 = bleed_px + safe_px, bleed_px + safe_px
    sx1, sy1 = px_w - bleed_px - safe_px, px_h - bleed_px - safe_px
    safe_w, safe_h = max(1.0, sx1 - sx0), max(1.0, sy1 - sy0)

    # --- caption: size, contrast and the rule under it carry three perturbations -------
    text_pt = spec.min_text_pt * 1.8
    if plan.has(IssueCode.TEXT_TOO_SMALL):
        text_pt = spec.min_text_pt / plan.magnitude_for(IssueCode.TEXT_TOO_SMALL)
    target_de = spec.min_contrast_delta_e * 3.0
    if plan.has(IssueCode.LOW_CONTRAST):
        target_de = spec.min_contrast_delta_e / plan.magnitude_for(IssueCode.LOW_CONTRAST)
    if legacy_grey_captions:  # how real_art and real_art_v2 were built
        ink = foreground_at_delta_e(background, target_de)
        achieved_de = delta_e76(background, ink)
    else:
        ink, achieved_de = caption_colour(background, target_de)
    stroke_pt = spec.min_stroke_pt * 2.0
    if plan.has(IssueCode.THIN_LINES):
        stroke_pt = spec.min_stroke_pt / plan.magnitude_for(IssueCode.THIN_LINES)
    text_px = max(4, round(_pt_to_px(text_pt, dpi)))
    stroke_px = max(1, round(_pt_to_px(stroke_pt, dpi)))

    # --- the illustration --------------------------------------------------------------
    art_zone_h = safe_h * 0.68
    side = max(8, round(min(safe_w, art_zone_h) * rng.uniform(0.7, 0.95)))
    if isinstance(svg, Path):
        art = _rasterise(svg, side)
        ix0, iy0, ix1, iy1 = _ink_box(art)
    else:
        art = _fit_raster(svg, side)
        ix0, iy0, ix1, iy1 = _visible_ink_box(art, background)
    ink_w, ink_h = ix1 - ix0, iy1 - iy0

    edge = None
    if plan.has(IssueCode.CONTENT_IN_SAFE_ZONE):
        depth = plan.magnitude_for(IssueCode.CONTENT_IN_SAFE_ZONE)
        edge = (
            plan.intrusion_edge
            if plan.intrusion_edge != "left"
            else rng.choice(("left", "right", "top", "bottom"))
        )
        outer = bleed_px + safe_px * (1.0 - depth)  # ink edge distance from canvas edge
        cx = sx0 + (safe_w - ink_w) / 2
        cy = sy0 + (art_zone_h - ink_h) / 2
        ink_x, ink_y = {
            "left": (outer, cy),
            "right": (px_w - outer - ink_w, cy),
            "top": (cx, outer),
            "bottom": (cx, px_h - outer - ink_h),
        }[edge]
    else:
        ink_x = sx0 + (safe_w - ink_w) / 2
        ink_y = sy0 + (art_zone_h - ink_h) / 2
    img.paste(art, (round(ink_x - ix0), round(ink_y - iy0)), art)

    # caption goes under the art, or over it when the art was pushed to the bottom edge
    try:
        font = ImageFont.load_default(size=text_px)
    except Exception:  # pragma: no cover - very old Pillow
        font = ImageFont.load_default()
    caption = rng.choice(CAPTIONS)
    tw = draw.textlength(caption, font=font)
    tx = sx0 + max(0.0, (safe_w - tw) / 2)
    ty = sy0 + 2 if edge == "bottom" else sy1 - text_px * 1.25 - stroke_px * 3
    draw.text((tx, ty), caption, font=font, fill=ink)
    rule_y = ty + text_px * 1.25 + stroke_px
    draw.line((sx0 + safe_w * 0.2, rule_y, sx1 - safe_w * 0.2, rule_y), fill=ink, width=stroke_px)
    return img, dpi, achieved_de


def drawn_perturbations(
    plan: CasePlan, achieved_de: float, legacy_grey_captions: bool = False
) -> tuple[Perturbation, ...]:
    """The plan's perturbations, with LOW_CONTRAST at the contrast actually drawn."""
    if not plan.has(IssueCode.LOW_CONTRAST) or legacy_grey_captions:
        return plan.perturbations
    return tuple(
        Perturbation(code=p.code, magnitude=round(plan.spec.min_contrast_delta_e / achieved_de, 3))
        if p.code is IssueCode.LOW_CONTRAST
        else p
        for p in plan.perturbations
    )


def build(
    n: int,
    seed: int,
    clean_fraction: float,
    out_name: str,
    exclude: list[Path] | None = None,
    legacy_grey_captions: bool = False,
) -> Path:
    """Render the set. `exclude` lists earlier manifests whose illustrations must not be
    reused: a validation set that repeats artwork the checks were diagnosed on is not a
    validation set (per-case seeds overlap between nearby base seeds, so without this a
    second set shared 175 of 442 illustrations with the first)."""
    files = _art_files()
    used = {
        json.loads(line)["art"]
        for manifest in exclude or []
        for line in manifest.open(encoding="utf-8")
    }
    out_dir = ROOT / out_name
    manifest = ROOT / f"{out_name}.jsonl"
    plans = plan_cases(n, seed=seed, clean_fraction=clean_fraction)
    rows = []
    for i, plan in enumerate(plans):
        rng = random.Random(plan.seed)
        style = STYLES[i % len(STYLES)]
        while True:
            svg = rng.choice(files[style])
            while f"{style}/{svg.name}" in used:
                svg = rng.choice(files[style])
            used.add(f"{style}/{svg.name}") if exclude else None
            try:
                img, dpi, achieved_de = render_real(
                    plan, svg, plan.seed, legacy_grey_captions=legacy_grey_captions
                )
                break
            except Exception as exc:  # noqa: BLE001 - a few library SVGs crash cairosvg
                # Only ever reached by art that failed; earlier sets never hit it, so they
                # rebuild byte-for-byte.
                print(f"skipped unrenderable art {style}/{svg.name}: {type(exc).__name__}")
                used.add(f"{style}/{svg.name}")
        perturbations = drawn_perturbations(plan, achieved_de, legacy_grey_captions)
        path = save_case(img, dpi, plan, out_dir)
        label = GoldLabel(case_id=plan.case_id, perturbations=perturbations, split=plan.split)
        rows.append(
            {
                "case_id": plan.case_id,
                "image_path": str(path.relative_to(ROOT.parent.parent)).replace("\\", "/"),
                "order": plan.order.model_dump(),
                "label": label.model_dump(mode="json"),
                "rendered_dpi": round(dpi, 3),
                "style": style,
                "art": f"{style}/{svg.name}",
            }
        )
    with manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the real-artwork evaluation set.")
    ap.add_argument("-n", type=int, default=450)
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--clean-fraction", type=float, default=0.60)
    ap.add_argument("--out", type=str, default="real_art")
    ap.add_argument(
        "--legacy-grey-captions",
        action="store_true",
        help="reproduce real_art / real_art_v2, whose LOW_CONTRAST labels are unreliable",
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="manifest whose illustrations must not be reused; repeatable",
    )
    args = ap.parse_args()
    exclude = [ROOT / m for m in args.exclude] if args.exclude else None
    manifest = build(
        args.n, args.seed, args.clean_fraction, args.out, exclude, args.legacy_grey_captions
    )
    rows = [json.loads(line) for line in manifest.open(encoding="utf-8")]
    clean = sum(1 for r in rows if not any(p["magnitude"] > 1 for p in r["label"]["perturbations"]))
    print(f"wrote {len(rows)} cases -> {manifest}  ({clean} clean)")


if __name__ == "__main__":
    main()
