"""Build the demo's report data and sample files from saved eval runs.

    python -m capstone.demo.build_reports

Reads run files in capstone/evals/runs/ (gitignored; they exist where the evals were
run) and writes capstone/demo/static/reports.json plus a small set of sample images in
capstone/demo/samples/. Both outputs are committed, so the demo runs without the runs.

Every figure on the reports page comes from a named run file listed below; nothing is
typed in by hand except the cost assumptions, which say where they come from.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from capstone.evals.metrics import clopper_pearson_upper

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "capstone" / "evals" / "runs"
DATA = REPO / "capstone" / "data"
HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
OUT = HERE / "static" / "reports.json"

# Sealed rounds: (name, note, files, rules_only run, cv_decider run). Scored once each.
ROUNDS = [
    (
        "Real art, round 1",
        "450 unseen illustrations; strict-CMYK rule",
        450,
        "20260924T044528Z-rules_only",
        "20260924T045122Z-cv_decider",
    ),
    (
        "Real art, round 2",
        "450 unseen illustrations; strict-CMYK rule",
        450,
        "20260924T063152Z-rules_only",
        "20260924T063851Z-cv_decider",
    ),
    (
        "Real art, round 3",
        "1,000 unseen illustrations; strict-CMYK rule",
        1000,
        "20260924T100436Z-rules_only",
        "20260924T094418Z-cv_decider",
    ),
    (
        "Real art, round 4",
        "1,000 unseen illustrations; RGB converted",
        1000,
        "20260924T162434Z-rules_only",
        "20260924T160855Z-cv_decider",
    ),
    (
        "Customer mistakes, round 4",
        "480 real-art stickers put through customer processes",
        480,
        "20260924T162838Z-rules_only",
        "20260924T161512Z-cv_decider",
    ),
    (
        "Synthetic holdout, round 3",
        "600 generated files",
        600,
        "20260924T100843Z-rules_only",
        "20260924T095019Z-cv_decider",
    ),
    (
        "Synthetic, defects on any edge, round 3",
        "400 generated files",
        400,
        "20260924T101149Z-rules_only",
        "20260924T095442Z-cv_decider",
    ),
]

MISTAKE_TEXT = {
    "print_ready": ("Print-ready file", "A careful designer's CMYK TIFF with bleed. The control."),
    "screenshot": ("Screenshot", "Copied from a browser or preview: screen pixels, no DPI."),
    "messaging_app": (
        "Sent over a chat app",
        "Long side capped at 1600 px, recompressed, metadata stripped.",
    ),
    "jpeg_resaves": (
        "Re-saved as JPEG",
        "Opened and saved as JPEG a few times. Should still pass.",
    ),
    "via_gif": ("Through a GIF tool", "Reduced to a small dithered palette, exported to PNG."),
    "gif_upload": ("Uploaded as .gif", "Not a print format."),
    "background_removed": ("Background remover", "Background made transparent, soft haloed edge."),
    "trim_size_export": (
        "Exported without bleed",
        "Design-tool default: the ordered size, no bleed.",
    ),
}


def _report(run: str) -> dict:
    report = json.loads((RUNS / f"{run}.json").read_text(encoding="utf-8"))["report"]
    # Round 1 and 2 files carry the old normal-approximation bound; recompute exactly.
    report["false_approve_ci_upper_95"] = round(
        clopper_pearson_upper(report["approved_defective"], report["approved"]), 4
    )
    report["meets_constraint"] = report["meets_constraint_sc002"]
    report["meets_target"] = report["meets_target_sc001"]
    return report


def _pixels(path: Path) -> int:
    with Image.open(path) as img:
        return img.width * img.height


def _thumb(src: Path, name: str, long_side: int = 480) -> str:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img.seek(0)
        img = img.convert("RGBA")
        img.thumbnail((long_side, long_side))
        backdrop = Image.new("RGBA", img.size, (233, 232, 228, 255))
        backdrop.alpha_composite(img)
        backdrop.convert("RGB").save(SAMPLES / name, "JPEG", quality=85)
    return name


def mistakes_section(run: str, manifest: Path) -> tuple[list[dict], list[dict]]:
    rows = {
        json.loads(line)["case_id"]: json.loads(line) for line in manifest.open(encoding="utf-8")
    }
    results = json.loads((RUNS / f"{run}.json").read_text(encoding="utf-8"))["results"]
    by_process: dict[str, dict] = {}
    samples: list[dict] = []
    for r in results:
        row = rows[r["case_id"]]
        process = row["process"]
        stats = by_process.setdefault(
            process, {"n": 0, "approved": 0, "fix": 0, "review": 0, "wrong": 0, "rows": []}
        )
        stats["n"] += 1
        v = r["verdict"]["verdict"]
        stats[{"APPROVE": "approved", "REQUEST_FIX": "fix", "ESCALATE": "review"}[v]] += 1
        stats["wrong"] += int(r["false_approve"]) + int(r["false_reject"])
        stats["rows"].append((row, r))

    gallery = []
    for process, (title, what) in MISTAKE_TEXT.items():
        stats = by_process.get(process)
        if stats is None:
            continue
        # The example is the first correctly decided file, preferring a defective one
        # where the process produces defects: it shows what the process does, and the
        # counts sit next to it so one example cannot stand in for the rate.
        correct = [
            (row, r) for row, r in stats["rows"] if not r["false_approve"] and not r["false_reject"]
        ]
        defective = [
            (row, r)
            for row, r in correct
            if row["label"]["perturbations"]
            and any(p["magnitude"] > 1 for p in row["label"]["perturbations"])
        ]
        pool = defective or correct or stats["rows"]
        # Among those, the largest file: a 180 px banner is a true example but a poor
        # picture. Size is chosen for legibility; the verdict is not a criterion.
        row, r = max(pool, key=lambda pr: _pixels(REPO / pr[0]["image_path"]))
        src = REPO / row["image_path"]
        example = _thumb(src, f"{process}-thumb.jpg")
        sample_name = f"{process}{src.suffix}"
        if src.suffix == ".tif":  # lossless LZW: same pixels, a fraction of the bytes
            with Image.open(src) as img:
                img.save(SAMPLES / sample_name, compression="tiff_lzw", dpi=img.info.get("dpi"))
        else:
            (SAMPLES / sample_name).write_bytes(src.read_bytes())
        samples.append(
            {
                "file": sample_name,
                "thumb": example,
                "title": title,
                "description": what,
                "order": row["order"],
            }
        )
        right = stats["n"] - stats["wrong"]
        gallery.append(
            {
                "process": process,
                "title": title,
                "what": what,
                "n": stats["n"],
                "approved": stats["approved"],
                "fix": stats["fix"],
                "review": stats["review"],
                "example": example,
                "verdict_text": f"{right} of {stats['n']} decided as the label says.",
            }
        )
    return gallery, samples


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mistakes-run", required=True, help="cv_decider run file stem")
    ap.add_argument("--mistakes-manifest", default="mistakes_v2.jsonl")
    args = ap.parse_args()

    rounds = [
        {"name": name, "note": note, "n": n, "rules_only": _report(r), "cv_decider": _report(c)}
        for name, note, n, r, c in ROUNDS
    ]
    gallery, samples = mistakes_section(args.mistakes_run, DATA / args.mistakes_manifest)
    real = rounds[3]["cv_decider"]
    data = {
        "headline": [
            {
                "value": f"{real['auto_approve_rate']:.1%}",
                "label": "clean real artwork auto-approved",
            },
            {
                "value": f"{real['false_approve_rate']:.1%}",
                "label": f"wrong approvals ({real['approved_defective']} of {real['approved']})",
            },
            {
                "value": f"{real['false_approve_ci_upper_95']:.1%}",
                "label": "95% upper bound on that rate",
            },
            {"value": "$0.00", "label": "model cost per file"},
        ],
        "rounds": rounds,
        "mistakes": gallery,
        "cost": {
            "files_per_day": 4000,
            "rows": [
                {"name": "This checker", "per_file": 0.0, "source": "no model call; runs on a CPU"},
                {
                    "name": "Claude vision agent, one call",
                    "per_file": 0.0066,
                    "source": "measured on this project's own runs (results.md)",
                },
                {
                    "name": "Claude vision agent, tool loop",
                    "per_file": 0.0117,
                    "source": "measured on this project's own runs (results.md)",
                },
            ],
            "note": (
                "Volume is the brief's assumption (4,000 files a day), not a measured "
                "figure. Compute and hosting are not included on either side."
            ),
        },
        "limits": [
            (
                "No real customer uploads yet. The artwork is real illustration, but it was "
                "laid out and labelled by this project's builder, which shares assumptions "
                "with the checks."
            ),
            (
                f"Real art, round 4: {real['approved_defective']} wrong approvals in "
                f"{real['approved']}. The exact 95% upper bound is "
                f"{real['false_approve_ci_upper_95']:.1%}, under the 1% target, on "
                "artwork this project laid out; real uploads may behave differently."
            ),
            (
                "Rounds 1-3 were scored under a strict CMYK rule; round 4 under the "
                "current rule, where RGB is converted rather than rejected."
            ),
            (
                "Known misses: grey artwork that the text detector boxes as text, near-white "
                "artwork on white stock, a hairline inside thick ink, and low-resolution art "
                "upscaled to look high-resolution."
            ),
            (
                "The customer-mistake gallery is a stress test, not a rate estimate: the "
                "processes are real, the labels are ours."
            ),
        ],
    }
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    (SAMPLES / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print(f"wrote {OUT} and {len(samples)} samples -> {SAMPLES}")


if __name__ == "__main__":
    main()
