"""Build the demo's lettering-review page data: what a person said vs what the system did.

    python -m capstone.demo.build_lettering

Rule loop round 1 and 1b (capstone/docs/rule-loop.md) asked a person, for each
TEXT_TOO_SMALL-blocked clean file of ai_art_v1, whether the flagged lettering is real
text, garbled or decorative, not text, or can't tell. The answers are committed in
capstone/evals/labels/. This module puts each answer next to what the shipped pipeline
does with the same file today, and writes:

    capstone/demo/static/lettering.json      every item, the summary, the gallery picks
    capstone/demo/samples/lettering/*.jpg    images for the gallery picks only

The images are gitignored. `--rebuild` recreates only the labelled cases of ai_art_v1,
from the seeds it was sealed with, instead of all 1,000 (the full fetch reads ~1,100
members from ~1,000 DiffusionDB archives over HTTP and takes hours):

    python -m capstone.demo.build_lettering --rebuild

Each rebuilt case must reproduce its sealed manifest row exactly — case order, image,
cut-out, DPI, label and file path — or the build stops. The full-set route
(`fetch_ai_art -n 1100 --seed 20261010`, then `ai_art --seed 20261011`) gives the same
files.

Two more checks guard the join between a label and an image: the image must be exactly
the size recorded with the label (`canvas`), and the label's box must lie inside it.

**System confidence is a measurement, not a probability.** A file blocked by a rule
carries a fixed confidence (0.95) that was never calibrated, so it is not shown. What is
shown is how far the text is under the minimum: the ratio to the minimum, and the gap in
pixels at print resolution. Within half a pixel, rasterising alone could account for it.

The four ai_art_v2 items in round 1b are controls; they are left out rather than
rebuilding ai_art_v2 and the three fetches its exclusions depend on. The summary counts
still include them, because they come from the label files, not from images.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from capstone.src.deciders import LogisticModel, decide_explained
from capstone.src.product_specs import get_spec
from capstone.src.schemas import IssueCode, OrderMetadata, PreflightCase, Severity
from capstone.tools.bucket1_metadata import effective_dpi, read_metadata

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "capstone" / "evals" / "labels"
MANIFEST = REPO / "capstone" / "data" / "ai_art_v1.jsonl"
HERE = Path(__file__).resolve().parent
OUT = HERE / "static" / "lettering.json"
IMAGES = HERE / "samples" / "lettering"

PT_PER_INCH = 72.0

# What each answer means for printing. "can't tell" is neither.
BLOCKS = {"real_small": True, "garbled": False, "not_text": False, "cant_tell": None}
ANSWER_TEXT = {
    "real_small": "Real text, too small",
    "garbled": "Garbled or decorative lettering",
    "not_text": "Not text",
    "cant_tell": "Can't tell",
}

# Gallery picks per category, most legible first (tallest flagged box). The rule is fixed
# here so the choice is not made by looking at verdicts.
PICKS = {"flipped": 6, "agree": 5, "policy": 5, "false_positive": 4, "unresolved": 3, "control": 2}

# Items left out of the gallery, with the reason. They stay in the table. t005, t015,
# t034, t057, t074 and t099 were looked at; t030, t068, t100 and t103 were left out on
# their prompt text alone, under the same brand rule, without viewing the image.
# Regions on our own caption are left out by rule (`own_caption`), not listed here.
BRAND = "prompt names a real brand or product; keeps third-party marks off the demo"
EXCLUDE: dict[str, str] = {
    "t005": "photorealistic people in uniform; " + BRAND,
    "t015": BRAND,
    "t034": "imitates a real game logo; " + BRAND,
    "t057": "imitates a real sports-team logo; " + BRAND,
    "t074": BRAND,
    "t099": BRAND,
    "t030": BRAND,
    "t068": BRAND,
    "t100": BRAND,
    "t103": BRAND,
}

CATEGORY_TEXT = {
    "agree": ("Agree", "Person: real text, too small. System: blocked. Both say send it back."),
    "policy": (
        "Policy question",
        "Person: garbled or decorative. System: blocked. Whether AI lettering must be "
        "readable is a product decision, not a measurement.",
    ),
    "false_positive": (
        "System false positive",
        "Person: not text. The detector boxed part of the artwork as a line of text.",
    ),
    "unresolved": ("Unresolved", "Person could not tell from the picture."),
    "flipped": (
        "Flipped",
        "Shown twice, unannounced. The person gave different answers.",
    ),
    "control": (
        "Control",
        "A caption we injected below the minimum. The right answer is known: real text, too small.",
    ),
}


# --------------------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------------------


def _read(name: str) -> list[dict]:
    return [json.loads(line) for line in (LABELS / name).open(encoding="utf-8")]


def summary(r1: list[dict], r1b: list[dict]) -> dict:
    """Headline counts, from the label files alone."""
    c1 = [r for r in r1 if r["kind"] == "control"]
    c1b = [r for r in r1b if r["kind"] == "control"]
    repeats = [r for r in r1b if r["kind"] == "repeat"]
    same = [r for r in repeats if r["answer"] == r["round1_answer"]]
    changed = [r for r in repeats if r["answer"] != r["round1_answer"]]
    crossed = [
        r
        for r in changed
        if BLOCKS[r["answer"]] is not None
        and BLOCKS[r["round1_answer"]] is not None
        and BLOCKS[r["answer"]] != BLOCKS[r["round1_answer"]]
    ]
    return {
        "round1_items": len(r1),
        "round1_controls_right": sum(r["answer"] == "real_small" for r in c1),
        "round1_controls": len(c1),
        "round1b_items": len(r1b),
        "round1b_controls_right": sum(r["answer"] == "real_small" for r in c1b),
        "round1b_controls": len(c1b),
        "repeats": len(repeats),
        "repeats_same": len(same),
        "repeats_changed": len(changed),
        "repeats_crossed_print_line": len(crossed),
        "main_answers": dict(Counter(r["answer"] for r in r1 if r["kind"] == "main")),
    }


def items(r1: list[dict], r1b: list[dict]) -> list[dict]:
    """One entry per judged region, with every answer it received."""
    later: dict[str, dict] = {r["round1_item"]: r for r in r1b if r.get("round1_item")}
    out: list[dict] = []
    for r in r1:
        second = later.get(r["item"])
        entry = {
            "item": r["item"],
            "case_id": r["case_id"],
            "region": r["region"],
            "canvas": r["canvas"],
            "kind": r["kind"],
            "round1": r["answer"],
            "round1b": second["answer"] if second else None,
            "round1b_kind": second["kind"] if second else None,
            "round1b_shown_px": second.get("shown_px") if second else None,
        }
        # Reshown items replace their round-1 answer (it was judged from a view too small
        # to read); repeats keep round 1 and are marked if they changed. As pre-registered.
        if entry["round1b_kind"] == "reshown":
            entry["answer"] = entry["round1b"]
        else:
            entry["answer"] = entry["round1"]
        out.append(entry)
    for r in r1b:
        if r["kind"] == "control" and not r.get("round1_item"):
            out.append(
                {
                    "item": r["item"],
                    "case_id": r["case_id"],
                    "region": r["region"],
                    "canvas": r["canvas"],
                    "kind": "control",
                    "set": r.get("set", "ai_art_v1"),
                    "round1": None,
                    "round1b": r["answer"],
                    "round1b_kind": "control",
                    "round1b_shown_px": r.get("shown_px"),
                    "answer": r["answer"],
                }
            )
    assert len({e["item"] for e in out}) == len(out), "duplicate item ids"
    return out


def category(e: dict) -> str:
    if e["kind"] == "control":
        return "control"
    if e["round1b_kind"] == "repeat" and e["round1b"] != e["round1"]:
        return "flipped"
    return {
        "real_small": "agree",
        "garbled": "policy",
        "not_text": "false_positive",
        "cant_tell": "unresolved",
    }[e["answer"]]


# --------------------------------------------------------------------------------------
# The system side
# --------------------------------------------------------------------------------------


def _overlap(a: list[int], b: list[int]) -> float:
    """Intersection over the smaller box: 1.0 when one sits inside the other."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return inter / small


def system_side(row: dict, label_region: list[int], model: LogisticModel) -> dict:
    order = OrderMetadata.model_validate(row["order"])
    path = REPO / row["image_path"]
    case = PreflightCase(case_id=row["case_id"], image_path=path, order=order)
    verdict, p, issues, _features = decide_explained(case, model, model.threshold)
    spec = get_spec(order.product_id)
    dpi = effective_dpi(read_metadata(path), spec, order) or float(spec.min_dpi)

    text = [
        i for i in issues if i.code is IssueCode.TEXT_TOO_SMALL and i.severity is Severity.BLOCKING
    ]
    measurement = None
    if text and text[0].evidence.measured is not None:
        ev = text[0].evidence
        gap_pt = ev.required - ev.measured
        measurement = {
            "measured_pt": ev.measured,
            "required_pt": ev.required,
            "ratio": round(ev.measured / ev.required, 3),
            "gap_px": round(gap_pt / PT_PER_INCH * dpi, 2),
            "region": list(ev.region) if ev.region else None,
            "same_box_as_label": (
                bool(ev.region) and _overlap(list(ev.region), label_region) >= 0.5
            ),
        }
    return {
        "verdict": verdict.verdict.value,
        "blocking": sorted(
            {i.code.value for i in verdict.issues if i.severity is Severity.BLOCKING}
        ),
        "text": measurement,
        "dpi": round(dpi, 1),
        "product": spec.display_name,
        "min_text_pt": spec.min_text_pt,
    }


def confidence_note(m: dict | None) -> str:
    """Plain words for how firm the measurement is. Never a probability."""
    if m is None:
        return "No text-size finding on this file today."
    if m["gap_px"] < 0.5:
        return (
            "Within half a pixel under the limit: rasterising alone could explain it. The rule "
            "still blocks, because it measured under."
        )
    if m["ratio"] >= 0.85:
        return f"Close to the limit: {m['ratio']:.0%} of the minimum, {m['gap_px']:.1f} px short."
    return f"Clearly under: {m['ratio']:.0%} of the minimum, {m['gap_px']:.1f} px short."


# --------------------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------------------


def _save(img: Image.Image, name: str) -> str:
    IMAGES.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(IMAGES / name, "JPEG", quality=86)
    return name


def whole_view(src: Image.Image, box: list[int], name: str, long_side: int = 520) -> str:
    """The whole sticker, with the judged box drawn on it."""
    img = src.convert("RGB")
    scale = min(1.0, long_side / max(img.size))
    img = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS
    )
    x0, y0, x1, y1 = (round(v * scale) for v in box)
    pad = 4
    draw = ImageDraw.Draw(img)
    for w in range(3):
        draw.rectangle(
            (x0 - pad - w, y0 - pad - w, x1 + pad + w, y1 + pad + w), outline=(226, 35, 26)
        )
    return _save(img, name)


def close_view(src: Image.Image, box: list[int], name: str, letter_px: int = 44) -> str:
    """The round-1b close-up: about 5x the box height, its width plus 30% (rule-loop.md)."""
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half_w, half_h = w * 1.3 / 2, h * 5 / 2
    crop = src.convert("RGB").crop(
        (
            max(0, round(cx - half_w)),
            max(0, round(cy - half_h)),
            min(src.width, round(cx + half_w)),
            min(src.height, round(cy + half_h)),
        )
    )
    scale = min(letter_px / h, 620 / max(1, crop.width))
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    return _save(crop.resize(size, Image.LANCZOS), name)


def round1_view(
    src: Image.Image, box: list[int], name: str, tile_px: int = 640
) -> tuple[str, float]:
    """A reconstruction of the round-1 close-up: padded by twice the box's width.

    For a wide, thin caption this shrinks the letters to a few pixels, which is the display
    fault that voided round 1's controls (rule-loop.md). Returns the letter height as shown.
    """
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    crop = src.convert("RGB").crop(
        (
            max(0, x0 - 2 * w),
            max(0, y0 - 2 * w),
            min(src.width, x1 + 2 * w),
            min(src.height, y1 + 2 * w),
        )
    )
    scale = tile_px / max(crop.width, crop.height)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    return _save(crop.resize(size, Image.LANCZOS), name), round(h * scale, 1)


# --------------------------------------------------------------------------------------
# Our own caption vs the AI image's lettering
# --------------------------------------------------------------------------------------


def _builders():
    """Import the set builders. real_art imports cairosvg at load time, which needs a cairo
    library Windows usually lacks; the raster path used for AI art never calls it."""
    try:
        import cairosvg  # noqa: F401
    except (ImportError, OSError):  # not installed, or installed without its library
        import sys
        import types

        sys.modules["cairosvg"] = types.ModuleType("cairosvg")
    from capstone.data import ai_art, fetch_ai_art, generate, real_art

    return ai_art, fetch_ai_art, generate, real_art


def caption_bands(plan, canvas_h: int) -> list[tuple[float, float]]:  # noqa: ANN001
    """Vertical extents where real_art.render_real draws our caption, from the plan alone.

    The caption is centred under the art at `sy1 - 1.25 * text_px - 3 * stroke_px`, or at
    the top of the safe area when an intrusion pushed the art to the bottom edge. Which
    edge that is can be a random draw, so both positions are returned for intrusion cases.
    """
    _, _, _, real_art = _builders()
    from capstone.src.schemas import IssueCode as Code

    spec = plan.spec
    dpi = float(spec.min_dpi)
    if plan.has(Code.LOW_RESOLUTION):
        dpi = spec.min_dpi / plan.magnitude_for(Code.LOW_RESOLUTION)
    bleed_in = spec.bleed_in
    if plan.has(Code.MISSING_BLEED):
        bleed_in = spec.bleed_in / plan.magnitude_for(Code.MISSING_BLEED)
    text_pt = spec.min_text_pt * 1.8
    if plan.has(Code.TEXT_TOO_SMALL):
        text_pt = spec.min_text_pt / plan.magnitude_for(Code.TEXT_TOO_SMALL)
    text_px = max(4, round(text_pt / PT_PER_INCH * dpi))
    stroke_px = real_art.rule_stroke_px(plan, dpi)
    edge = bleed_in * dpi + spec.safe_zone_in * dpi
    under = canvas_h - edge - text_px * 1.25 - stroke_px * 3
    bands = [(under, under + text_px * 1.25)]
    if plan.has(Code.CONTENT_IN_SAFE_ZONE):
        bands.append((edge + 2, edge + 2 + text_px * 1.25))
    return bands


def on_caption(region: list[int], bands: list[tuple[float, float]]) -> bool:
    """True when at least half the region's height lies inside a caption band."""
    y0, y1 = region[1], region[3]
    height = max(1, y1 - y0)
    return any(max(0.0, min(y1, b1) - max(y0, b0)) / height >= 0.5 for b0, b1 in bands)


# --------------------------------------------------------------------------------------
# Rebuilding only the labelled cases
# --------------------------------------------------------------------------------------

# ai_art_v1 as sealed at 18d0756 (commit message): fetch seed, build seed, clean fraction.
FETCH_N, FETCH_SEED = 1100, 20261010
BUILD_SEED, CLEAN_FRACTION = 20261011, 0.60


def sealed_plans() -> tuple[list[dict], list]:
    """The manifest rows and the generator's plans for them, checked to be in step."""
    _, _, generate, _ = _builders()
    rows = [json.loads(line) for line in MANIFEST.open(encoding="utf-8")]
    plans = generate.plan_cases(len(rows), seed=BUILD_SEED, clean_fraction=CLEAN_FRACTION)
    if [p.case_id for p in plans] != [r["case_id"] for r in rows]:
        raise SystemExit("plan order differs from the sealed manifest; wrong seed or generator")
    return rows, plans


def rebuild(case_ids: set[str]) -> None:
    """Recreate the named ai_art_v1 cases and check each against the sealed manifest."""
    ai_art, fetch_ai_art, generate, real_art = _builders()
    AI, SOURCE, cut_out = ai_art.AI, ai_art.SOURCE, ai_art.cut_out
    save_case = generate.save_case
    ROOT, drawn_perturbations, render_real = (
        real_art.ROOT,
        real_art.drawn_perturbations,
        real_art.render_real,
    )

    rows, plans = sealed_plans()
    wanted = {r["art"].split("/", 1)[1] for r in rows if r["case_id"] in case_ids}

    folder = AI / SOURCE
    missing = {name for name in wanted if not (folder / name).exists()}
    if missing:
        chosen = [r for r in fetch_ai_art.select(FETCH_N, FETCH_SEED) if r["image_name"] in missing]
        if len(chosen) != len(missing):
            raise SystemExit(f"{len(missing) - len(chosen)} images are not in the sealed draw")
        print(f"fetching {len(chosen)} images from DiffusionDB")
        fetch_ai_art.fetch(chosen, folder)

    for plan, row in zip(plans, rows, strict=True):
        if row["case_id"] not in case_ids:
            continue
        name = row["art"].split("/", 1)[1]
        with Image.open(folder / name) as source_img:
            art, cutout = cut_out(source_img, False)  # v1: flags off
        img, dpi, achieved_de = render_real(plan, art, plan.seed)
        label = drawn_perturbations(plan, achieved_de, drawn_stroke_dpi=None)
        path = save_case(img, dpi, plan, ROOT / "ai_art_v1")
        rebuilt = {
            "cutout": cutout,
            "rendered_dpi": round(dpi, 3),
            "perturbations": [p.model_dump(mode="json") for p in label],
            "image_path": str(path.relative_to(ROOT.parent.parent)).replace("\\", "/"),
        }
        sealed = {
            "cutout": row["cutout"],
            "rendered_dpi": row["rendered_dpi"],
            "perturbations": row["label"]["perturbations"],
            "image_path": row["image_path"],
        }
        if rebuilt != sealed:
            raise SystemExit(
                f"{row['case_id']}: rebuild differs from the sealed row\n{rebuilt}\n{sealed}"
            )
    print(f"rebuilt {len(case_ids)} cases; every one matches its sealed manifest row")


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true", help="recreate the labelled cases first")
    args = ap.parse_args()

    r1, r1b = _read("lettering_round1.jsonl"), _read("lettering_round1b.jsonl")
    if args.rebuild:
        rebuild({e["case_id"] for e in items(r1, r1b) if e.get("set", "ai_art_v1") == "ai_art_v1"})
    manifest = {
        json.loads(line)["case_id"]: json.loads(line) for line in MANIFEST.open(encoding="utf-8")
    }
    model = LogisticModel.load()
    _rows, plans = sealed_plans()
    plan_of = {p.case_id: p for p in plans}

    entries = []
    for e in items(r1, r1b):
        if e.get("set", "ai_art_v1") != "ai_art_v1":
            continue
        row = manifest[e["case_id"]]
        path = REPO / row["image_path"]
        if not path.exists():
            raise SystemExit(f"{path} missing: rebuild ai_art_v1 first (see the module docstring)")
        with Image.open(path) as img:
            if list(img.size) != e["canvas"]:
                raise SystemExit(
                    f"{e['item']} {e['case_id']}: image is {img.size}, label says {e['canvas']}. "
                    "The rebuild does not match what was labelled."
                )
        x0, y0, x1, y1 = e["region"]
        if not (0 <= x0 < x1 <= e["canvas"][0] and 0 <= y0 < y1 <= e["canvas"][1]):
            raise SystemExit(f"{e['item']}: label box {e['region']} is outside the image")
        system = system_side(row, e["region"], model)
        plan = plan_of[e["case_id"]]
        caption = on_caption(e["region"], caption_bands(plan, e["canvas"][1]))
        if e["kind"] == "control" and not caption:
            # Controls are our caption by construction; a miss means the band rule is wrong.
            raise SystemExit(f"{e['item']}: control region is not on the caption band")
        text_mag = plan.magnitude_for(IssueCode.TEXT_TOO_SMALL) or None
        e.update(
            {
                "category": category(e),
                "own_caption": caption,
                # Font size relative to the minimum as drawn: 1.8 when untouched.
                "caption_font_ratio": round(1 / text_mag, 3) if text_mag else 1.8,
                "system": system,
                "confidence_note": confidence_note(system["text"]),
                "prompt": (row.get("prompt") or "")[:140],
                "box_height_px": y1 - y0,
            }
        )
        entries.append(e)

    # Gallery picks: fixed count per category, tallest box first, exclusions honoured.
    gallery: list[dict] = []
    for cat, n in PICKS.items():
        pool = sorted(
            (
                e
                for e in entries
                if e["category"] == cat
                and e["item"] not in EXCLUDE
                # outside controls, show the AI image's lettering, not our caption
                and (cat == "control" or not e["own_caption"])
            ),
            key=lambda e: (-e["box_height_px"], e["item"]),
        )
        gallery += pool[:n]

    for e in gallery:
        row = manifest[e["case_id"]]
        with Image.open(REPO / row["image_path"]) as img:
            img.load()
            e["images"] = {
                "whole": whole_view(img, e["region"], f"{e['item']}-whole.jpg"),
                "close": close_view(img, e["region"], f"{e['item']}-close.jpg"),
            }

    # The display fault: one round-1 control, shown the round-1 way and the round-1b way.
    fault = None
    small_controls = [
        e for e in entries if e["kind"] == "control" and e["round1"] and e["round1"] != "real_small"
    ] or [e for e in entries if e["kind"] == "control" and e["round1"]]
    if small_controls:
        e = max(small_controls, key=lambda x: (x["region"][2] - x["region"][0], x["item"]))
        with Image.open(REPO / manifest[e["case_id"]]["image_path"]) as img:
            img.load()
            # The shown height depends on a tile size the round-1 page did not record, so
            # it is not reported; rule-loop.md's own figures are quoted on the page instead.
            before, _shown = round1_view(img, e["region"], f"{e['item']}-round1-view.jpg")
            after = close_view(img, e["region"], f"{e['item']}-round1b-view.jpg")
        fault = {
            "item": e["item"],
            "before": before,
            "after": after,
            "round1_answer": e["round1"],
        }

    counts = Counter(e["category"] for e in entries)
    caption_counts = Counter(e["category"] for e in entries if e["own_caption"])
    data = {
        "summary": summary(r1, r1b),
        "categories": {
            k: {
                "title": t,
                "text": d,
                "count": counts.get(k, 0),
                "own_caption": caption_counts.get(k, 0),
            }
            for k, (t, d) in CATEGORY_TEXT.items()
        },
        "answer_text": ANSWER_TEXT,
        "system_changed": [
            e["item"] for e in entries if e["kind"] != "control" and not e["system"]["text"]
        ],
        "excluded": EXCLUDE,
        "gallery": [e["item"] for e in gallery],
        "fault": fault,
        "items": entries,
        "note": (
            "Four round-1b controls came from ai_art_v2 and are counted in the summary but "
            "not shown. Images are rebuilt from the recorded seeds; each was checked against "
            "the size recorded with its label."
        ),
    }
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {OUT}: {len(entries)} items, {len(gallery)} gallery cards -> {IMAGES}")
    print("categories:", dict(counts))
    print("of which our own caption:", dict(caption_counts))
    changed = data["system_changed"]
    print(f"items with no text-size finding today: {len(changed)} {changed[:10]}")
    same = sum(
        bool(e["system"]["text"] and e["system"]["text"]["same_box_as_label"]) for e in entries
    )
    print(f"today's box matches the judged box: {same} of {len(entries)}")


if __name__ == "__main__":
    main()
