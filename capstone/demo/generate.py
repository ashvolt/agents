"""Live AI sticker generation for the demo, from whichever provider has a key.

The point is to show the checker on a brand-new AI image, the fastest-growing kind of
upload, the moment it is made. Keys come from the environment only (CLAUDE.md):

- Cloudflare Workers AI: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (FLUX.1-schnell)
- Hugging Face Inference: HF_TOKEN (FLUX.1-schnell)

With neither set, `available()` is False and the demo hides the feature.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request

CLOUDFLARE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
TIMEOUT_S = 90


class GenerationError(RuntimeError):
    pass


# Cloudflare's error code for a prompt its safety filter refuses. It misfires: on
# 2026-09-26 "sticker of a pizza slice with the text PIZZA TIME" was refused as NSFW.
CLOUDFLARE_REFUSED = 8007


def _explain(status: int, detail: str) -> str:
    """A message a customer can act on, not the provider's raw error body."""
    try:
        codes = {e.get("code") for e in json.loads(detail).get("errors", [])}
    except (ValueError, AttributeError):
        codes = set()
    if CLOUDFLARE_REFUSED in codes:
        return (
            "The image provider's safety filter refused this prompt. It sometimes refuses "
            "harmless prompts; try rewording it."
        )
    return f"{status} from the image provider: {detail}"


def provider() -> str | None:
    if os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN"):
        return "cloudflare"
    if os.environ.get("HF_TOKEN"):
        return "huggingface"
    return None


def available() -> bool:
    return provider() is not None


def _post(url: str, body: dict, token: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:600].decode(errors="replace")
        raise GenerationError(_explain(exc.code, detail)) from exc


def generate(prompt: str, seed: int | None = None) -> tuple[bytes, str]:
    """Return (image bytes, provider name). Raises GenerationError."""
    name = provider()
    if name == "cloudflare":
        account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{CLOUDFLARE_MODEL}"
        # No seed: this model rejects the field (error 5006, checked live 2026-09-26),
        # so Cloudflare images are not reproducible from a seed.
        body: dict = {"prompt": prompt, "steps": 4}
        raw, _ = _post(url, body, os.environ["CLOUDFLARE_API_TOKEN"])
        payload = json.loads(raw)
        image = (payload.get("result") or {}).get("image")
        if not image:
            raise GenerationError(f"no image in the response: {str(payload)[:300]}")
        return base64.b64decode(image), name
    if name == "huggingface":
        body = {"inputs": prompt}
        if seed is not None:
            body["parameters"] = {"seed": seed}
        raw, content_type = _post(HF_MODEL_URL, body, os.environ["HF_TOKEN"])
        if not content_type.startswith("image/"):
            raise GenerationError(f"expected an image, got {content_type}: {raw[:300]!r}")
        return raw, name
    raise GenerationError("no image provider configured (see capstone/docs/demo.md)")


__all__ = ["GenerationError", "available", "generate", "provider"]
