"""Fetch real, openly licensed artwork for the real-art evaluation set.

    python -m capstone.data.fetch_art

Three illustration libraries, three styles, all professionally designed and all licensed
for reuse. They are fetched from the npm registry at pinned versions and extracted to
`capstone/data/art_cache/` (gitignored — the art is not redistributed from this repo).

| Library  | Style                         | Licence of the artwork |
|----------|-------------------------------|------------------------|
| OpenMoji | flat colour, black outlines   | CC BY-SA 4.0 — HfG Schwäbisch Gmünd |
| Twemoji  | flat colour, no outlines      | CC BY 4.0 — Twitter, Inc. and contributors |
| Noto     | gradients, shading, highlights | Apache 2.0 — Google |

Noto is deliberately in the set. Every scene and fidelity number so far assumes flat
colour; shaded artwork is the style most likely to break that assumption, and it should
be measured rather than avoided.

This is real design work, not real *customer uploads*: no reviewer decisions come with it,
and it is cleaner than what customers send. See real-art.md.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "art_cache"

SOURCES = {
    "openmoji": "https://registry.npmjs.org/openmoji/-/openmoji-17.0.0.tgz",
    "twemoji": "https://registry.npmjs.org/@twemoji/svg/-/svg-15.0.0.tgz",
    "noto": "https://registry.npmjs.org/@iconify-json/noto/-/noto-1.2.8.tgz",
}


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _extract_svgs(name: str, blob: bytes, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            path = member.name
            if name == "noto" and path.endswith("icons.json"):
                data = json.load(tar.extractfile(member))  # type: ignore[arg-type]
                width, height = data.get("width", 128), data.get("height", 128)
                for icon, spec in data["icons"].items():
                    w, h = spec.get("width", width), spec.get("height", height)
                    svg = (
                        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
                        f"{spec['body']}</svg>"
                    )
                    (out / f"{icon}.svg").write_text(svg, encoding="utf-8")
                    count += 1
                continue
            keep = (name == "openmoji" and "/color/svg/" in path and path.endswith(".svg")) or (
                name == "twemoji" and path.endswith(".svg")
            )
            if keep:
                (out / Path(path).name).write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]
                count += 1
    return count


def fetch() -> dict[str, dict[str, object]]:
    report: dict[str, dict[str, object]] = {}
    for name, url in SOURCES.items():
        blob = _download(url)
        n = _extract_svgs(name, blob, CACHE / name)
        report[name] = {"url": url, "sha256": hashlib.sha256(blob).hexdigest(), "svgs": n}
    (CACHE / "SOURCES.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    for name, info in fetch().items():
        print(f"{name:9s} {info['svgs']:5d} svgs  sha256 {str(info['sha256'])[:16]}...")
