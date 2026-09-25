"""AI-generated artwork evaluation set: Stable Diffusion sticker art laid out as uploads.

    python -m capstone.data.fetch_ai_art -n 1100 --seed 20261010     # once
    python -m capstone.data.ai_art -n 1000 --seed 20261011 --out ai_art_v1

The same layout and labels as real_art.py (plan, perturbations, caption, rule, saving),
with the vector illustration swapped for an image from DiffusionDB (CC0) whose prompt
asked for a sticker, decal, logo, badge or emblem. These are what people actually typed
and what Stable Diffusion actually made: soft edges, JPEG-like noise, garbled lettering,
frames that fill the whole square.

**The background.** A generated image is a rectangle. When its border is one flat colour
(a sticker on white, a logo on a plain field) that colour is cut out where it touches the
border, the way a customer's background remover would, and the illustration's own ink is
what gets placed. When the border is busy (a painted scene, a gradient) the whole
rectangle is the artwork, as it would be if a customer uploaded it as is. Each row
records which (`cutout`), so results can be split by it. Either way the illustration is
placed by its *visible* ink (pixels more than 24 levels from the sticker background), so
an injected safe-zone intrusion crosses the line with something that shows, not with an
off-white margin.

What cannot be labelled by construction is, again, the image's own content: garbled
lettering at 5 pt, hairlines, a pale highlight. And unlike the vector libraries there is
no high-resolution original to referee against (real-art.md §4). Findings on files
labelled clean are reported separately, not scored away.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from capstone.data.generate import plan_cases, save_case
from capstone.data.real_art import ROOT, drawn_perturbations, render_real
from capstone.src.schemas import GoldLabel

AI = ROOT / "ai_cache"
SOURCE = "diffusiondb"  # a fetch in ai_cache: the folder and its <name>_index.jsonl

BORDER_TOLERANCE = 24  # levels per channel; the stroke mask's own foreground threshold
FLAT_BORDER = 0.75  # share of border pixels within tolerance of their median; art that
# touches the edge in places still leaves a flat background (0.81 on a white logo field)


RESIDUE_PX = 16  # kept fragments smaller than this, after the edge erode, are residue


def cut_out(img: Image.Image, clean_edges: bool = False) -> tuple[Image.Image, bool]:
    """RGBA with a flat border colour removed where it touches the border.

    Returns (image, cutout). A busy border is left alone and the image is opaque.

    `clean_edges` does what a background remover's matting does: drops the 1 px ring of
    antialiased pixels between the art and the removed colour, and fragments of fewer
    than RESIDUE_PX pixels left stranded in the background. Without it that halo and
    residue measure as sub-minimum strokes (5 of 20 sampled THIN_LINES rejects on
    ai_art_v1, ai-art.md section 4). Off by default so ai_art_v1 rebuilds byte-for-byte.
    """
    rgb = np.asarray(img.convert("RGB")).astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    colour = np.median(border, axis=0)
    near_border = np.abs(border - colour).max(axis=1) <= BORDER_TOLERANCE
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    if near_border.mean() < FLAT_BORDER:
        return Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha])), False
    near = (np.abs(rgb - colour).max(axis=2) <= BORDER_TOLERANCE).astype(np.uint8)
    _, labels = cv2.connectedComponents(near, connectivity=4)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    touching = np.isin(labels, edge[edge != 0]) & (near == 1)
    alpha[touching] = 0
    if clean_edges:
        kept = cv2.erode((alpha > 0).astype(np.uint8), np.ones((3, 3), np.uint8))
        count, parts, stats, _ = cv2.connectedComponentsWithStats(kept, connectivity=8)
        small = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < RESIDUE_PX)
        kept[np.isin(parts, small[small != 0])] = 0
        alpha = np.where(kept > 0, 255, 0).astype(np.uint8)
    if not (alpha > 0).any():  # nothing but background: keep it opaque rather than empty
        return Image.fromarray(np.dstack([rgb.astype(np.uint8), np.full_like(alpha, 255)])), False
    return Image.fromarray(np.dstack([rgb.astype(np.uint8), alpha])), True


def _sources(exclude: list[Path] | None, source: str = SOURCE) -> list[dict]:
    index = AI / f"{source}_index.jsonl"
    if not index.exists():
        raise SystemExit(f"no AI artwork in {index}. Run: python -m capstone.data.fetch_ai_art")
    used = {
        json.loads(line).get("art")
        for manifest in exclude or []
        for line in manifest.open(encoding="utf-8")
    }
    rows = [json.loads(line) for line in index.open(encoding="utf-8")]
    return [
        r
        for r in rows
        if (AI / source / r["image_name"]).exists() and f"diffusiondb/{r['image_name']}" not in used
    ]


def build(
    n: int,
    seed: int,
    clean_fraction: float,
    out_name: str,
    exclude: list[Path] | None = None,
    source: str = SOURCE,
    clean_edges: bool = False,
    drawn_stroke_labels: bool = False,
) -> Path:
    """Render the set. Each generated image is used at most once in a set, and never if an
    `exclude` manifest used it. The two flags are off for ai_art_v1 and on from v2."""
    sources = _sources(exclude, source)
    if len(sources) < n:
        raise SystemExit(f"{n} cases need {n} images; {len(sources)} available. Fetch more.")
    random.Random(seed).shuffle(sources)
    out_dir = ROOT / out_name
    manifest = ROOT / f"{out_name}.jsonl"
    plans = plan_cases(n, seed=seed, clean_fraction=clean_fraction)
    rows = []
    for plan, image in zip(plans, sources[:n], strict=True):
        art, cutout = cut_out(Image.open(AI / source / image["image_name"]), clean_edges)
        img, dpi, achieved_de = render_real(plan, art, plan.seed)
        path = save_case(img, dpi, plan, out_dir)
        label = GoldLabel(
            case_id=plan.case_id,
            perturbations=drawn_perturbations(
                plan, achieved_de, drawn_stroke_dpi=dpi if drawn_stroke_labels else None
            ),
            split=plan.split,
        )
        rows.append(
            {
                "case_id": plan.case_id,
                "image_path": str(path.relative_to(ROOT.parent.parent)).replace("\\", "/"),
                "order": plan.order.model_dump(),
                "label": label.model_dump(mode="json"),
                "rendered_dpi": round(dpi, 3),
                "style": "diffusiondb",
                "art": f"diffusiondb/{image['image_name']}",
                "cutout": cutout,
                "prompt": image["prompt"],
            }
        )
    with manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the AI-generated artwork evaluation set.")
    ap.add_argument("-n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--clean-fraction", type=float, default=0.60)
    ap.add_argument("--out", type=str, default="ai_art_v1")
    ap.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="manifest whose images must not be reused; repeatable",
    )
    ap.add_argument("--source", default=SOURCE, help="fetch in ai_cache to draw images from")
    ap.add_argument("--clean-edges", action="store_true", help="matte the cut-out (from v2)")
    ap.add_argument(
        "--drawn-stroke-labels",
        action="store_true",
        help="label THIN_LINES from the rule's drawn pixel width (from v2)",
    )
    args = ap.parse_args()
    exclude = [ROOT / m for m in args.exclude] if args.exclude else None
    manifest = build(
        args.n,
        args.seed,
        args.clean_fraction,
        args.out,
        exclude,
        args.source,
        args.clean_edges,
        args.drawn_stroke_labels,
    )
    rows = [json.loads(line) for line in manifest.open(encoding="utf-8")]
    cut = sum(r["cutout"] for r in rows)
    print(f"{len(rows)} cases -> {manifest} ({cut} cut out, {len(rows) - cut} whole rectangle)")


if __name__ == "__main__":
    main()
