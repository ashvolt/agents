"""The web demo's API: shape of the answer, and bad input handled without a 500."""

from __future__ import annotations

import io

import pytest
from PIL import Image

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from capstone.demo.app import app  # noqa: E402

client = TestClient(app)


def _png(size: tuple[int, int] = (975, 975), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, "white").save(buf, "PNG", dpi=(300, 300))
    return buf.getvalue()


def _check(data: bytes, name: str = "a.png", product: str = "die-cut-sticker"):
    return client.post(
        "/api/check",
        files={"file": (name, data, "image/png")},
        data={"product_id": product, "width_in": 3, "height_in": 3},
    )


def test_products_are_listed() -> None:
    ids = {p["id"] for p in client.get("/api/products").json()}
    assert "die-cut-sticker" in ids


def test_a_check_returns_verdict_preview_and_zero_cost() -> None:
    body = _check(_png()).json()
    assert body["verdict"] in {"APPROVE", "REQUEST_FIX", "ESCALATE"}
    assert body["cost_usd"] == 0.0
    assert body["preview"].startswith("data:image/png;base64,")


def test_rgb_upload_is_an_advisory_note_not_a_fix() -> None:
    body = _check(_png()).json()
    rgb = [i for i in body["issues"] if i["code"] == "WRONG_COLOR_MODE"]
    assert all(i["severity"] == "ADVISORY" for i in rgb)


def test_garbage_is_escalated_not_a_server_error() -> None:
    response = _check(b"not an image")
    assert response.status_code == 200
    assert response.json()["verdict"] == "ESCALATE"


def test_unknown_product_is_a_client_error() -> None:
    assert _check(_png(), product="mystery-mug").status_code == 400


def test_sample_paths_cannot_escape_the_samples_folder() -> None:
    assert client.get("/samples/..%2Fapp.py").status_code == 404


GEN_FORM = {"prompt": "a sticker", "product_id": "die-cut-sticker", "width_in": 3, "height_in": 3}


def test_generation_is_hidden_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert client.get("/api/generate").json()["available"] is False


def test_generated_image_is_checked_like_an_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    from capstone.demo import generate as ai

    monkeypatch.setattr(ai, "generate", lambda prompt, seed=None: (_png((1024, 1024)), "fake"))
    body = client.post(
        "/api/generate",
        data=GEN_FORM,
    ).json()
    assert body["verdict"] in {"APPROVE", "REQUEST_FIX", "ESCALATE"}
    assert body["generated"]["provider"] == "fake"


def test_provider_failure_is_a_502_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    from capstone.demo import generate as ai

    def fail(prompt, seed=None):  # noqa: ANN001, ANN202
        raise ai.GenerationError("402 insufficient balance")

    monkeypatch.setattr(ai, "generate", fail)
    response = client.post(
        "/api/generate",
        data=GEN_FORM,
    )
    assert response.status_code == 502
