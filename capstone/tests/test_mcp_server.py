"""The MCP server: the shipped pipeline, reached through the MCP protocol.

Every test goes through a real MCP client connected in-process (`mcp.Client(server)`),
so tool listing, argument validation and result encoding are the protocol's own.
Needs the optional `mcp` dependency; skipped without it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from capstone.mcp_server import mcp as server  # noqa: E402
from capstone.src.deciders import LogisticModel, decide_explained  # noqa: E402
from capstone.src.schemas import OrderMetadata, PreflightCase  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "demo" / "samples"
SAMPLE_ORDERS = json.loads((SAMPLES / "samples.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREFLIGHT_MCP_ROOT", str(SAMPLES))


def _call(name: str, arguments: dict) -> dict:
    async def go() -> dict:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments)
            assert not result.is_error, result.content
            return result.structured_content or {}

    return asyncio.run(go())


def _args(sample: dict) -> dict:
    order = sample["order"]
    return {
        "path": sample["file"],
        "product_id": order["product_id"],
        "width_in": order["width_in"],
        "height_in": order["height_in"],
    }


def test_every_tool_is_listed_and_read_only() -> None:
    async def go():  # noqa: ANN202
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == {
        "list_products",
        "get_product_spec",
        "check_artwork",
        "inspect_file",
        "analyse_pixels",
    }
    for tool in tools.values():
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
    # the agent's own verdict tool is internal to the pipeline, never exposed
    assert "submit_verdict" not in tools


def test_products_are_listed_with_their_specs() -> None:
    products = _call("list_products", {})["products"]
    ids = {p["product_id"] for p in products}
    assert "die-cut-sticker" in ids
    spec = _call("get_product_spec", {"product_id": "die-cut-sticker"})
    assert spec["min_dpi"] > 0 and spec["min_text_pt"] > 0


@pytest.mark.parametrize("sample", SAMPLE_ORDERS, ids=[s["file"] for s in SAMPLE_ORDERS])
def test_check_artwork_gives_the_pipelines_own_verdict(sample: dict) -> None:
    # the server adds nothing and loses nothing: same verdict as calling the decider directly
    got = _call("check_artwork", _args(sample))
    o = sample["order"]
    order = OrderMetadata(
        order_id="MCP",
        product_id=o["product_id"],
        width_in=o["width_in"],
        height_in=o["height_in"],
        quantity=1,
    )
    model = LogisticModel.load()
    verdict, *_ = decide_explained(
        PreflightCase(case_id="mcp", image_path=SAMPLES / sample["file"], order=order),
        model,
        model.threshold,
    )
    assert got["verdict"] == verdict.verdict.value
    assert got["cost_usd"] == 0.0


def test_a_print_ready_file_is_approved_and_a_screenshot_is_sent_back() -> None:
    by_file = {s["file"]: s for s in SAMPLE_ORDERS}
    assert _call("check_artwork", _args(by_file["print_ready.tif"]))["verdict"] == "APPROVE"
    shot = _call("check_artwork", _args(by_file["screenshot.png"]))
    assert shot["verdict"] == "REQUEST_FIX"
    assert "LOW_RESOLUTION" in {i["code"] for i in shot["issues"]}
    assert shot["customer_message"]


def test_measurement_tools_return_the_registry_payloads() -> None:
    sample = next(s for s in SAMPLE_ORDERS if s["file"] == "screenshot.png")
    meta = _call("inspect_file", _args(sample))
    assert meta["readable"] is True and meta["effective_dpi"] < meta["min_dpi_required"]
    pixels = _call("analyse_pixels", _args(sample))
    assert "error" not in pixels


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../../../pyproject.toml", "../samples/../../../CLAUDE.md"],
)
def test_files_outside_the_root_are_never_read(path: str) -> None:
    got = _call(
        "check_artwork",
        {"path": path, "product_id": "die-cut-sticker", "width_in": 3, "height_in": 3},
    )
    assert got["error"] == "outside_root"


def test_bad_input_is_a_payload_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = {"product_id": "die-cut-sticker", "width_in": 3, "height_in": 3}
    assert _call("check_artwork", {"path": "missing.png", **common})["error"] == "not_found"
    sample = SAMPLE_ORDERS[0]
    unknown = {**_args(sample), "product_id": "no-such-product"}
    assert _call("check_artwork", unknown)["error"] == "unknown_product"
    zero = {**_args(sample), "width_in": 0}
    assert _call("check_artwork", zero)["error"] == "bad_order"
    # a file that is not an image still gets a verdict, never an exception
    (tmp_path / "notes.png").write_text("not an image")
    monkeypatch.setenv("PREFLIGHT_MCP_ROOT", str(tmp_path))
    garbage = _call("check_artwork", {"path": "notes.png", **common})
    assert garbage["verdict"] == "ESCALATE"
    assert [i["code"] for i in garbage["issues"]] == ["UNREADABLE_FILE"]


def test_a_symlink_out_of_the_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.png").symlink_to(SAMPLES / "print_ready.tif")  # a real image, outside root
    monkeypatch.setenv("PREFLIGHT_MCP_ROOT", str(root))
    got = _call(
        "check_artwork",
        {"path": "link.png", "product_id": "die-cut-sticker", "width_in": 3, "height_in": 3},
    )
    assert got["error"] == "outside_root"
