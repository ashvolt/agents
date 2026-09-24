"""Customer-mistake simulator: good artwork, broken the way customers really break it.

    python -m capstone.data.mistakes -n 480 --seed 20261007 --out mistakes_v1 \\
        --exclude real_art.jsonl --exclude real_art_v2.jsonl \\
        --exclude real_art_v3.jsonl --exclude real_art_v4.jsonl

Every other set injects defects with our own drawing code, so the checks are graded on
our own assumptions (limits.md §1). Here each file starts as a clean, print-ready sticker
made from real illustration (real_art.py) and then goes through one thing a customer
actually does: screenshots it, sends it over a messaging app, re-saves it as JPEG,
converts it through a GIF, runs it through a background remover, or exports it from a
design tool without bleed.

The label is a fact about that process, not about our drawing: pixels per inch after a
screenshot, bleed after a trim-size export, alpha after background removal. A process
that changes nothing a spec measures (JPEG re-saves, GIF palettes) is labelled clean,
and exists to catch the opposite failure: a checker that rejects good art because it
has been compressed.

Colour mode is not a label here: every product converts RGB itself (product_specs,
`converted_color_modes`), so an RGB upload is advisory, never a defect.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import random
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from capstone.data.generate import BINARY_MAGNITUDE, CasePlan, plan_cases
from capstone.data.real_art import ROOT, STYLES, _art_files, render_real
from capstone.src.schemas import GoldLabel, IssueCode, Perturbation

SCREEN_PPI = 96.0  # CSS pixels per inch: what a browser screenshot resolves to at 100%
MESSAGING_LONG_SIDE = 1600  # common messaging-app resize for photos sent "as photo"

Result = tuple[Image.Image, str, dict, tuple[Perturbation, ...]]


def _canvas_in(plan: CasePlan) -> tuple[float, float]:
    o, s = plan.order, plan.spec
    return o.width_in + 2 * s.bleed_in, o.height_in + 2 * s.bleed_in


def _resolution(plan: CasePlan, effective_dpi: float) -> tuple[Perturbation, ...]:
    """LOW_RESOLUTION at the resolution the file will actually print at."""
    return (
        Perturbation(
            code=IssueCode.LOW_RESOLUTION, magnitude=round(plan.spec.min_dpi / effective_dpi, 3)
        ),
    )


def print_ready(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """The control: what a careful designer uploads."""
    return img.convert("CMYK"), "tif", {"dpi": (dpi, dpi)}, ()


def screenshot(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Copied from a browser or a design tool preview: screen pixels, no DPI metadata."""
    zoom = rng.uniform(0.6, 1.5)
    w_in, h_in = _canvas_in(plan)
    size = (max(8, round(w_in * SCREEN_PPI * zoom)), max(8, round(h_in * SCREEN_PPI * zoom)))
    shot = img.resize(size, Image.LANCZOS)
    return shot, "png", {}, _resolution(plan, size[0] / w_in)


def messaging_app(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Sent to themselves over a chat app: long side capped, recompressed, metadata gone."""
    w, h = img.size
    scale = min(1.0, MESSAGING_LONG_SIDE / max(w, h))
    sent = img.resize((max(8, round(w * scale)), max(8, round(h * scale))), Image.LANCZOS)
    w_in, _ = _canvas_in(plan)
    perts = _resolution(plan, sent.width / w_in) if scale < 1.0 else ()
    return sent, "jpg", {"quality": 70}, perts


def jpeg_resaves(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Opened and saved as JPEG a few times. Changes nothing a spec measures."""
    out = img.convert("RGB")
    for _ in range(rng.randint(2, 4)):
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=rng.randint(55, 80))
        out = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return out, "jpg", {"quality": 85, "dpi": (dpi, dpi)}, ()


def via_gif(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Made in a GIF tool and exported to PNG: a small palette, dithered."""
    colours = rng.choice((32, 64, 128, 256))
    gif = img.convert("RGB").quantize(colors=colours, dither=Image.Dither.FLOYDSTEINBERG)
    return gif.convert("RGB"), "png", {"dpi": (dpi, dpi)}, ()


def gif_upload(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Uploaded as a .gif: not a print format. Must not be approved."""
    gif = img.convert("RGB").quantize(colors=256, dither=Image.Dither.FLOYDSTEINBERG)
    perts = (Perturbation(code=IssueCode.UNREADABLE_FILE, magnitude=BINARY_MAGNITUDE),)
    return gif, "gif", {}, perts


def background_removed(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Run through a one-click background remover: the background becomes transparent,
    with the soft, haloed edge those tools leave."""
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    corner = arr[:4, :4].reshape(-1, 3).mean(axis=0)
    distance = np.abs(arr - corner).max(axis=2)
    alpha = np.clip((distance - 6) * 12, 0, 255).astype(np.uint8)  # soft ramp = halo
    rgba = np.dstack([arr.astype(np.uint8), alpha])
    out = Image.fromarray(rgba, "RGBA")
    perts = ()
    if not plan.spec.allows_transparency and (alpha < 255).any():
        perts = (Perturbation(code=IssueCode.UNINTENDED_TRANSPARENCY, magnitude=BINARY_MAGNITUDE),)
    return out, "png", {"dpi": (dpi, dpi)}, perts


def trim_size_export(img: Image.Image, plan: CasePlan, dpi: float, rng: random.Random) -> Result:
    """Designed at the ordered size and exported without bleed, the default in most
    online design tools."""
    bleed_px = round(plan.spec.bleed_in * dpi)
    cropped = img.crop((bleed_px, bleed_px, img.width - bleed_px, img.height - bleed_px))
    perts = ()
    if plan.spec.bleed_in > 0:
        perts = (Perturbation(code=IssueCode.MISSING_BLEED, magnitude=BINARY_MAGNITUDE),)
    return cropped, "png", {"dpi": (dpi, dpi)}, perts


PROCESSES: dict[str, Callable[..., Result]] = {
    "print_ready": print_ready,
    "screenshot": screenshot,
    "messaging_app": messaging_app,
    "jpeg_resaves": jpeg_resaves,
    "via_gif": via_gif,
    "gif_upload": gif_upload,
    "background_removed": background_removed,
    "trim_size_export": trim_size_export,
}


def build(n: int, seed: int, out_name: str, exclude: list[Path]) -> Path:
    files = _art_files()
    used = {
        json.loads(line)["art"] for manifest in exclude for line in manifest.open(encoding="utf-8")
    }
    out_dir = ROOT / out_name
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = ROOT / f"{out_name}.jsonl"
    names = list(PROCESSES)
    # Clean plans only: the defect comes from the process, never from the drawing.
    plans = [
        dataclasses.replace(p, perturbations=())
        for p in plan_cases(n, seed=seed, clean_fraction=1.0)
    ]
    rows = []
    for i, plan in enumerate(plans):
        rng = random.Random(plan.seed)
        style = STYLES[i % len(STYLES)]
        svg = rng.choice(files[style])
        while f"{style}/{svg.name}" in used:
            svg = rng.choice(files[style])
        used.add(f"{style}/{svg.name}")
        base, dpi, _ = render_real(plan, svg, plan.seed)
        process = names[i % len(names)]
        img, ext, save_kw, perts = PROCESSES[process](base, plan, dpi, rng)
        path = out_dir / f"{plan.case_id}.{ext}"
        img.save(path, **save_kw)
        label = GoldLabel(case_id=plan.case_id, perturbations=perts, split=plan.split)
        rows.append(
            {
                "case_id": plan.case_id,
                "image_path": str(path.relative_to(ROOT.parent.parent)).replace("\\", "/"),
                "order": plan.order.model_dump(),
                "label": label.model_dump(mode="json"),
                "process": process,
                "style": style,
                "art": f"{style}/{svg.name}",
            }
        )
    with manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the customer-mistake set.")
    ap.add_argument("-n", type=int, default=480)
    ap.add_argument("--seed", type=int, default=20261007)
    ap.add_argument("--out", type=str, default="mistakes_v1")
    ap.add_argument("--exclude", action="append", default=[], help="manifest whose art to skip")
    args = ap.parse_args()
    manifest = build(args.n, args.seed, args.out, [ROOT / e for e in args.exclude])
    counts: dict[str, int] = {}
    for line in manifest.open(encoding="utf-8"):
        row = json.loads(line)
        counts[row["process"]] = counts.get(row["process"], 0) + 1
    print(f"wrote {sum(counts.values())} cases -> {manifest}")
    for name, count in counts.items():
        print(f"  {name:20s} {count}")


if __name__ == "__main__":
    main()
