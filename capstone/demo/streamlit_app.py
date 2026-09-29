"""The demo as a Streamlit page, for free hosting on Streamlit Community Cloud.

    pip install -r capstone/demo/requirements.txt
    streamlit run capstone/demo/streamlit_app.py

Same check as the FastAPI demo (app.py), through the same function (pipeline.check_file):
the shipped CV decider, no model, no API key, nothing stored. The upload lives in a
temporary directory for the length of the check. AI generation is not offered here, so
the hosted page never holds a provider key. Deployment steps: capstone/docs/demo.md.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Community Cloud runs this file as a script, so the repo root is not on the path.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import base64  # noqa: E402
import json  # noqa: E402

import streamlit as st  # noqa: E402

from capstone.demo.pipeline import MAX_UPLOAD_BYTES, SAMPLES, check_file  # noqa: E402
from capstone.src.product_specs import all_specs  # noqa: E402

VERDICT_STYLE = {
    "APPROVE": (st.success, "Approved: prints as is"),
    "REQUEST_FIX": (st.error, "Fix requested: sent back to the customer"),
    "ESCALATE": (st.warning, "Escalated: a person must look"),
}


@st.cache_data
def _samples() -> list[dict]:
    return json.loads((SAMPLES / "samples.json").read_text(encoding="utf-8"))


def _show(result: dict) -> None:
    show, headline = VERDICT_STYLE[result["verdict"]]
    show(f"**{headline}** · {result['elapsed_ms']} ms · $0.00 (no model called)")
    left, right = st.columns([3, 2])
    with left:
        if result["preview"]:
            png = base64.b64decode(result["preview"].split(",", 1)[1])
            st.image(png, caption="Red dashes: trim. Blue dashes: safe zone.")
        else:
            st.info("This file could not be drawn.")
    with right:
        if result["escalation_reason"]:
            st.markdown(f"**Escalation reason:** `{result['escalation_reason']}`")
        st.markdown("**Issues**" if result["issues"] else "**No issues found.**")
        for issue in result["issues"]:
            ev = issue["evidence"]
            measured = ev.get("measured")
            detail = (
                f" (measured {measured:g} {ev.get('unit', '')}, required {ev.get('required'):g})"
                if isinstance(measured, int | float) and isinstance(ev.get("required"), int | float)
                else ""
            )
            st.markdown(
                f"- `{issue['code']}` {issue['severity'].lower()}: {issue['message']}{detail}"
            )
            # The customer register, where the finding has one. Indented under the
            # reviewer's line so the two are visibly different things.
            advice = issue.get("advice")
            if advice:
                lines = [
                    f"  - *What the customer is told:* {advice['headline']}",
                    f"    **What to do:** {advice['action']}",
                ]
                if advice.get("avoid"):
                    lines.append(f"    **Please avoid:** {advice['avoid']}")
                st.markdown("\n".join(lines))
        if result["customer_message"]:
            st.markdown("**Message to the customer**")
            st.info(result["customer_message"])
    low, high = result["vision_cost_usd"]
    # "\\$" keeps Markdown from reading a pair of dollar signs as LaTeX.
    # p_defect is None when a hard rule decided before the model was consulted
    p = result["p_defect"]
    st.caption(
        f"A vision model would cost \\${low}–\\${high} a file on this project's runs; "
        "this check costs nothing."
        + (f" Model p(defect) = {p:.3f}." if p is not None else " Decided by a hard rule.")
    )


st.set_page_config(page_title="Artwork preflight", layout="wide")
st.title("Artwork preflight")
st.caption(
    "Checks print artwork against a product's specification before it is printed: "
    "resolution, bleed, safe zone, text size, strokes, contrast, transparency. "
    "Unofficial project, not affiliated with Sticker Mule. Uploads are not stored."
)

specs = {s.product_id: s for s in all_specs()}
samples_tab, upload_tab = st.tabs(["Try a sample", "Upload your own"])

with samples_tab:
    samples = _samples()
    titles = [s["title"] for s in samples]
    picked = samples[titles.index(st.selectbox("Sample file", titles))]
    order = picked["order"]
    st.markdown(
        f"{picked['description']}  \nOrdered as **{specs[order['product_id']].display_name}**, "
        f"{order['width_in']:g} × {order['height_in']:g} in."
    )
    if st.button("Check this sample", type="primary"):
        with st.spinner("Checking…"):
            result = check_file(
                SAMPLES / picked["file"],
                order["product_id"],
                order["width_in"],
                order["height_in"],
            )
        _show(result)

with upload_tab:
    upload = st.file_uploader(
        "Artwork (PNG, JPEG, TIFF, GIF, WebP; up to 40 MB)",
        type=["png", "jpg", "jpeg", "tif", "tiff", "gif", "webp"],
    )
    product_id = st.selectbox(
        "Product",
        list(specs),
        index=list(specs).index("die-cut-sticker"),
        format_func=lambda pid: specs[pid].display_name,
    )
    width_col, height_col = st.columns(2)
    width_in = width_col.number_input("Width (in)", 0.5, 120.0, 3.0, 0.25)
    height_in = height_col.number_input("Height (in)", 0.5, 120.0, 3.0, 0.25)
    if upload is not None and st.button("Check my file", type="primary"):
        if upload.size > MAX_UPLOAD_BYTES:
            st.error("File larger than 40 MB.")
        else:
            suffix = Path(upload.name).suffix.lower()[:8] or ".bin"
            with (
                tempfile.TemporaryDirectory(prefix="preflight-st-") as tmp,
                st.spinner("Checking…"),
            ):
                path = Path(tmp) / f"upload{suffix}"
                path.write_bytes(upload.getvalue())
                result = check_file(path, product_id, width_in, height_in)
            _show(result)
