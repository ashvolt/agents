"""Fetch real AI-generated sticker art from DiffusionDB (CC0), without downloading archives.

    python -m capstone.data.fetch_ai_art -n 300 --seed 20261010

DiffusionDB (poloclub/diffusiondb, CC0 1.0) holds 2 million Stable Diffusion images with
the prompts people actually typed. This picks images whose prompt asks for a sticker,
decal, logo, badge or emblem, and fetches only those: each archive is a zip of ~1,000
PNGs, so the zip's directory and the chosen members are read with HTTP range requests
(`RangeFile`) instead of downloading hundreds of megabytes per archive.

Output goes to capstone/data/ai_cache/ (gitignored): the images and an index.jsonl with
the prompt, generation settings and archive for each. Nothing here is committed except
this code.

Network: huggingface.co, and the CDN its downloads redirect to (us.aws.cdn.hf.co).
"""

from __future__ import annotations

import argparse
import io
import json
import random
import re
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "ai_cache"
BASE = "https://huggingface.co/datasets/poloclub/diffusiondb/resolve/main"
METADATA = "metadata.parquet"  # the 2M-image subset: 512-ish px PNGs in images/part-*.zip

STICKER_WORDS = re.compile(r"\b(sticker|stickers|decal|die[- ]cut|logo|badge|emblem|patch)\b", re.I)
# Prompts that ask for transparency, any subject: where painted checkerboards come from.
# Used to find real positives for the FAKE_TRANSPARENCY check (ai-art.md section 5).
TRANSPARENT_WORDS = re.compile(
    r"(transparent background|\bpng\b|no background|alpha channel|checkerboard)", re.I
)
PROMPTS = {"sticker": STICKER_WORDS, "transparent": TRANSPARENT_WORDS}
MAX_NSFW = 0.1  # both the image and the prompt score, as published with the dataset


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over HTTP range requests. Enough for zipfile."""

    def __init__(self, url: str) -> None:
        head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60)
        self.url = head.geturl()  # follow the redirect once; ranges go to the CDN
        self.size = int(head.headers["Content-Length"])
        self.pos = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if self.pos >= self.size:
            return b""
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        request = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end - 1}"})
        data = urllib.request.urlopen(request, timeout=120).read()
        self.pos += len(data)
        return data

    def readinto(self, buffer) -> int:  # noqa: ANN001 - io protocol
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _download(name: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{BASE}/{name}", timeout=600) as src:
            dest.write_bytes(src.read())
    return dest


def select(
    n: int, seed: int, pattern: re.Pattern = STICKER_WORDS, exclude: set[str] | None = None
) -> list[dict]:
    """Matching prompts, safe scores, one image per distinct prompt, seeded sample.

    `exclude` removes image names, and every image made from the same prompt, from the
    pool *before* sampling, so a fresh draw never reuses an image an earlier draw fetched
    or another seed of it. With the defaults this is the original draw.
    """
    import pyarrow.parquet as pq

    table = pq.read_table(
        _download(METADATA, CACHE / METADATA),
        columns=[
            "image_name",
            "prompt",
            "part_id",
            "seed",
            "step",
            "cfg",
            "sampler",
            "width",
            "height",
            "image_nsfw",
            "prompt_nsfw",
        ],
    ).to_pylist()
    # an excluded image's prompt is spent too: another seed of it is a near-duplicate
    seen: set[str] = {
        (row["prompt"] or "").strip().lower()
        for row in table
        if row["image_name"] in (exclude or ())
    }
    pool = []
    for row in table:
        prompt = (row["prompt"] or "").strip()
        if not pattern.search(prompt):
            continue
        if (row["image_nsfw"] or 0) > MAX_NSFW or (row["prompt_nsfw"] or 0) > MAX_NSFW:
            continue
        if prompt.lower() in seen:
            continue
        seen.add(prompt.lower())
        pool.append(row)
    rng = random.Random(seed)
    rng.shuffle(pool)
    return sorted(pool[:n], key=lambda r: (r["part_id"], r["image_name"]))


def fetch(rows: list[dict], out: Path) -> list[dict]:
    """Read each chosen member from its archive with range requests."""
    out.mkdir(parents=True, exist_ok=True)
    by_part: dict[int, list[dict]] = {}
    for row in rows:
        by_part.setdefault(int(row["part_id"]), []).append(row)
    fetched = []
    for part, members in sorted(by_part.items()):
        missing = [m for m in members if not (out / m["image_name"]).exists()]
        if missing:
            archive = zipfile.ZipFile(RangeFile(f"{BASE}/images/part-{part:06d}.zip"))
            names = set(archive.namelist())
            for m in missing:
                if m["image_name"] in names:
                    (out / m["image_name"]).write_bytes(archive.read(m["image_name"]))
        fetched += [m for m in members if (out / m["image_name"]).exists()]
        print(f"part {part:06d}: {len(members)} wanted, {len(fetched)} fetched so far")
    return fetched


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--prompts", choices=sorted(PROMPTS), default="sticker")
    ap.add_argument("--name", default="diffusiondb", help="output folder and index name")
    ap.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="index (in ai_cache) whose images must not be drawn again; repeatable",
    )
    args = ap.parse_args()
    exclude = {
        json.loads(line)["image_name"]
        for name in args.exclude or []
        for line in (CACHE / name).open(encoding="utf-8")
    }
    rows = fetch(select(args.n, args.seed, PROMPTS[args.prompts], exclude), CACHE / args.name)
    with (CACHE / f"{args.name}_index.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({k: row[k] for k in row}, default=str) + "\n")
    print(f"{len(rows)} images -> {CACHE / args.name}")


if __name__ == "__main__":
    main()
