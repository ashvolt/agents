"""Local web demo: upload artwork, get the verdict a customer and a reviewer would see.

    pip install -e ".[demo]"
    uvicorn capstone.demo.app:app --port 8000        # then open http://localhost:8000

Runs the shipped pipeline (the CV decider, no model, no API key) on the uploaded file
and returns the verdict, every issue with its measurement, the drafted customer
message, and the artwork with the trim line, safe zone and problem regions drawn on it.
Nothing is stored: the upload lives in a temporary directory for the length of the
request.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from capstone.demo import generate as ai
from capstone.demo import pipeline
from capstone.demo.pipeline import MAX_UPLOAD_BYTES, SAMPLES
from capstone.src.product_specs import UnknownProductError, all_specs

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
# Recorded by walkthrough/record.mjs, gitignored like every other build artefact: a 12 MB
# binary in the history would drift from the product on every demo change, which is the
# problem re-recording exists to avoid. Served when it is there, hidden when it is not.
WALKTHROUGH = HERE / "walkthrough" / "out" / "walkthrough.webm"

app = FastAPI(title="Artwork preflight demo")


def check_file(path: Path, product_id: str, width_in: float, height_in: float) -> dict:
    try:
        return pipeline.check_file(path, product_id, width_in, height_in)
    except UnknownProductError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/products")
def products() -> list[dict[str, object]]:
    return [
        {
            "id": s.product_id,
            "name": s.display_name,
            "min_dpi": s.min_dpi,
            "bleed_in": s.bleed_in,
            "transparency": s.allows_transparency,
        }
        for s in all_specs()
    ]


@app.post("/api/check")
async def check(
    file: Annotated[UploadFile, File()],
    product_id: Annotated[str, Form()],
    width_in: Annotated[float, Form(gt=0, le=120)],
    height_in: Annotated[float, Form(gt=0, le=120)],
) -> JSONResponse:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file larger than 40 MB")
    suffix = Path(file.filename or "upload").suffix.lower()[:8] or ".bin"
    with tempfile.TemporaryDirectory(prefix="preflight-demo-") as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        return JSONResponse(check_file(path, product_id, width_in, height_in))


@app.get("/api/generate")
def generate_status() -> dict[str, object]:
    return {"available": ai.available(), "provider": ai.provider()}


@app.post("/api/generate")
def generate_and_check(
    prompt: Annotated[str, Form(min_length=3, max_length=400)],
    product_id: Annotated[str, Form()],
    width_in: Annotated[float, Form(gt=0, le=120)],
    height_in: Annotated[float, Form(gt=0, le=120)],
) -> JSONResponse:
    """Make an AI sticker from a prompt, then check it as if a customer uploaded it."""
    started = time.perf_counter()
    try:
        image, provider = ai.generate(prompt)
    except ai.GenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    generated_ms = round((time.perf_counter() - started) * 1000)
    with tempfile.TemporaryDirectory(prefix="preflight-gen-") as tmp:
        with Image.open(io.BytesIO(image)) as img:
            suffix = ".png" if img.format == "PNG" else ".jpg"
        path = Path(tmp) / f"generated{suffix}"
        path.write_bytes(image)
        result = check_file(path, product_id, width_in, height_in)
    result["generated"] = {"provider": provider, "prompt": prompt, "ms": generated_ms}
    return JSONResponse(result)


@app.get("/api/samples")
def samples() -> list[dict[str, object]]:
    index = SAMPLES / "samples.json"
    return json.loads(index.read_text(encoding="utf-8")) if index.exists() else []


@app.get("/api/samples/{name}/check")
def check_sample(name: str) -> JSONResponse:
    for sample in samples():
        if sample["file"] == name:
            order = sample["order"]
            result = check_file(
                SAMPLES / name, order["product_id"], order["width_in"], order["height_in"]
            )
            return JSONResponse({**result, "sample": sample})
    raise HTTPException(status_code=404, detail="no such sample")


@app.get("/samples/{name}")
def sample_file(name: str) -> FileResponse:
    path = (SAMPLES / name).resolve()
    if path.parent != SAMPLES or not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/lettering-images/{name}")
def lettering_image(name: str) -> FileResponse:
    """Images for the lettering-review page (build_lettering.py). Same guard as samples."""
    folder = SAMPLES / "lettering"
    path = (folder / name).resolve()
    if path.parent != folder or not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/api/walkthrough")
def walkthrough_status() -> dict[str, object]:
    """Whether a recording exists, so the page can hide the section rather than show a
    broken player on a fresh clone."""
    return {"available": WALKTHROUGH.exists()}


@app.get("/walkthrough.webm")
def walkthrough_video() -> FileResponse:
    if not WALKTHROUGH.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "no recording yet. Run: node capstone/demo/walkthrough/record.mjs "
                "http://localhost:8000"
            ),
        )
    return FileResponse(WALKTHROUGH, media_type="video/webm")


@app.get("/lettering")
def lettering() -> FileResponse:
    return FileResponse(STATIC / "lettering.html")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/reports")
def reports() -> FileResponse:
    return FileResponse(STATIC / "reports.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
