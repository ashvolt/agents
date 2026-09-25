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

import base64
import io
import json
import tempfile
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

from capstone.demo import generate as ai
from capstone.src.deciders import LogisticModel, decide_explained
from capstone.src.product_specs import UnknownProductError, all_specs, get_spec
from capstone.src.schemas import Issue, OrderMetadata, PreflightCase, ProductSpec, Severity

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
SAMPLES = HERE / "samples"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
PREVIEW_LONG_SIDE = 900

# Measured on this project's own runs (capstone/docs/results.md): the Claude vision agent
# at $0.0066 a file (one call) to $0.0117 (tool loop). This pipeline makes no model call.
VISION_COST_PER_FILE = (0.0066, 0.0117)

app = FastAPI(title="Artwork preflight demo")
_MODEL: LogisticModel | None = None


def _model() -> LogisticModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = LogisticModel.load()
    return _MODEL


def _issue_json(issue: Issue) -> dict[str, object]:
    return {
        "code": issue.code.value,
        "severity": issue.severity.value,
        "message": issue.message,
        "evidence": issue.evidence.model_dump(mode="json", exclude_none=True),
    }


def annotate(path: Path, spec: ProductSpec, order: OrderMetadata, issues: list[Issue]) -> str:
    """The artwork as a PNG data URL, with trim, safe zone and issue regions drawn on it."""
    with Image.open(path) as src:
        src.seek(0)
        img = src.convert("RGBA")
    width, height = img.size
    scale = min(1.0, PREVIEW_LONG_SIDE / max(width, height))
    preview = img.resize((max(1, round(width * scale)), max(1, round(height * scale))))
    backdrop = Image.new("RGBA", preview.size, (236, 236, 236, 255))
    backdrop.alpha_composite(preview)  # transparent areas show as grey, not black
    draw = ImageDraw.Draw(backdrop)

    # Trim from the ordered size, as the pipeline locates it: centred on the canvas.
    dpi_x = width / (order.width_in + 2 * spec.bleed_in)
    bleed_x = max(0.0, (width - order.width_in * dpi_x) / 2) * scale
    bleed_y = max(0.0, (height - order.height_in * dpi_x) / 2) * scale
    safe = spec.safe_zone_in * dpi_x * scale
    w, h = preview.size
    _dashed_rect(draw, (bleed_x, bleed_y, w - bleed_x, h - bleed_y), (220, 38, 38, 255))
    _dashed_rect(
        draw,
        (bleed_x + safe, bleed_y + safe, w - bleed_x - safe, h - bleed_y - safe),
        (37, 99, 235, 255),
    )
    for issue in issues:
        region = issue.evidence.region
        if region is None:
            continue
        colour = (220, 38, 38, 255) if issue.severity is Severity.BLOCKING else (217, 119, 6, 255)
        x0, y0, x1, y1 = (v * scale for v in region)
        draw.rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), outline=colour, width=3)

    buf = io.BytesIO()
    backdrop.convert("RGB").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _dashed_rect(draw: ImageDraw.ImageDraw, box: tuple[float, ...], colour: tuple) -> None:
    x0, y0, x1, y1 = box
    dash, gap = 8, 5
    for a, b, fixed, horizontal in (
        (x0, x1, y0, True),
        (x0, x1, y1, True),
        (y0, y1, x0, False),
        (y0, y1, x1, False),
    ):
        pos = a
        while pos < b:
            end = min(pos + dash, b)
            if horizontal:
                draw.line((pos, fixed, end, fixed), fill=colour, width=2)
            else:
                draw.line((fixed, pos, fixed, end), fill=colour, width=2)
            pos = end + gap


def check_file(path: Path, product_id: str, width_in: float, height_in: float) -> dict:
    """Run the shipped pipeline on one file and shape the answer for the page."""
    try:
        spec = get_spec(product_id)
    except UnknownProductError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    order = OrderMetadata(
        order_id="DEMO", product_id=product_id, width_in=width_in, height_in=height_in, quantity=1
    )
    case = PreflightCase(case_id="demo", image_path=path, order=order)
    model = _model()
    started = time.perf_counter()
    verdict, p_defect, issues, _features = decide_explained(case, model, model.threshold)
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    shown = list(verdict.issues) or [i for i in issues if i.severity is Severity.ADVISORY]
    try:
        preview = annotate(path, spec, order, shown)
    except Exception:  # noqa: BLE001 - an unreadable file still gets a verdict, not a 500
        preview = None
    return {
        "verdict": verdict.verdict.value,
        "confidence": verdict.confidence,
        "escalation_reason": verdict.escalation_reason.value if verdict.escalation_reason else None,
        "issues": [_issue_json(i) for i in shown],
        "customer_message": verdict.customer_message,
        "p_defect": p_defect,
        "elapsed_ms": elapsed_ms,
        "cost_usd": 0.0,
        "vision_cost_usd": VISION_COST_PER_FILE,
        "product": spec.display_name,
        "preview": preview,
    }


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/reports")
def reports() -> FileResponse:
    return FileResponse(STATIC / "reports.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
