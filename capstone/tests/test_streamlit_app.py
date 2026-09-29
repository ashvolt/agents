"""The Streamlit page (the hosted demo): it runs, and it shows the pipeline's own verdict.

Driven through Streamlit's AppTest, which executes the script as Community Cloud would.
Needs `streamlit`; skipped without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from capstone.demo.pipeline import check_file  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "demo" / "streamlit_app.py"
SAMPLES = APP.parent / "samples"
SAMPLE_ORDERS = json.loads((SAMPLES / "samples.json").read_text(encoding="utf-8"))
HEADLINE = {"APPROVE": "success", "REQUEST_FIX": "error", "ESCALATE": "warning"}


def test_the_page_loads_without_error() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Artwork preflight"


@pytest.mark.parametrize("sample", SAMPLE_ORDERS, ids=[s["file"] for s in SAMPLE_ORDERS])
def test_each_sample_shows_the_pipelines_verdict(sample: dict) -> None:
    order = sample["order"]
    expected = check_file(
        SAMPLES / sample["file"], order["product_id"], order["width_in"], order["height_in"]
    )["verdict"]

    at = AppTest.from_file(str(APP), default_timeout=120).run()
    at.selectbox[0].select(sample["title"]).run()
    at.button[0].click().run()
    assert not at.exception
    banner = getattr(at, HEADLINE[expected])
    assert len(banner) == 1 and "$0.00" in banner[0].value
