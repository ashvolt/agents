"""The preflight checker as an MCP server: any MCP client can check artwork with it.

    pip install -e ".[mcp]"
    python -m capstone.mcp_server                    # stdio, for a desktop MCP client
    PREFLIGHT_MCP_ROOT=/path/to/artwork python -m capstone.mcp_server

PLAN.md day 8 (L6). The tools are the ones the pipeline already uses, exposed as they are:
the full verdict from the shipped CV decider, and the exact measurements behind it. No
model is called and nothing costs money; every tool is read-only.

Two boundaries, because the caller is a model acting for someone:

- **Files are read only under one root,** `PREFLIGHT_MCP_ROOT` (default: the directory the
  server was started in). A path outside it, a missing file, or a file over 40 MB returns
  an error payload, never an exception and never a read.
- **Nothing the artwork says reaches the caller.** The text detector finds where text is
  and how large; its recogniser is never run (text_detect.py). So instructions written
  into an image cannot travel through this server to the model on the other side.

Tools never raise: a failure is a JSON payload with `error` and `detail`, which a model
can read and act on (the same rule as registry.dispatch).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from capstone.src.deciders import LogisticModel, decide_explained
from capstone.src.product_specs import UnknownProductError, all_specs, get_spec
from capstone.src.schemas import Issue, OrderMetadata, PreflightCase, Severity
from capstone.tools.registry import tool_analyse_pixels, tool_get_product_spec, tool_inspect_file

MAX_FILE_BYTES = 40 * 1024 * 1024  # the demo's upload cap
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

mcp = MCPServer(
    name="artwork-preflight",
    instructions=(
        "Checks print artwork against a product's specification before it is printed. "
        "Call list_products to see product ids, then check_artwork with a file path and "
        "the ordered size in inches. Verdicts: APPROVE (print as is), REQUEST_FIX (send "
        "the customer_message), ESCALATE (a person must look). Every number in a result "
        "is measured from the file; do not restate a verdict the tools did not give."
    ),
)

_MODEL: LogisticModel | None = None


def _model() -> LogisticModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = LogisticModel.load()
    return _MODEL


def _root() -> Path:
    return Path(os.environ.get("PREFLIGHT_MCP_ROOT") or Path.cwd()).resolve()


def _resolve(path: str) -> Path | dict[str, Any]:
    """The file to read, or an error payload. Relative paths are taken from the root."""
    root = _root()
    candidate = Path(path)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not target.is_relative_to(root):
        return {"error": "outside_root", "detail": f"only files under {root} can be read"}
    if not target.is_file():
        return {"error": "not_found", "detail": f"no file at {target}"}
    if target.stat().st_size > MAX_FILE_BYTES:
        return {"error": "too_large", "detail": "file larger than 40 MB"}
    return target


def _order(product_id: str, width_in: float, height_in: float, quantity: int) -> OrderMetadata:
    return OrderMetadata(
        order_id="MCP",
        product_id=product_id,
        width_in=width_in,
        height_in=height_in,
        quantity=quantity,
    )


def _issue(issue: Issue) -> dict[str, Any]:
    e = issue.evidence
    return {
        "code": issue.code.value,
        "severity": issue.severity.value,
        "message": issue.message,
        "measured": e.measured,
        "required": e.required,
        "unit": e.unit,
        "region": list(e.region) if e.region else None,
        "note": e.note,
    }


def _prepare(
    path: str, product_id: str, width_in: float, height_in: float, quantity: int
) -> tuple[Path, OrderMetadata] | dict[str, Any]:
    if width_in <= 0 or height_in <= 0 or quantity < 1:
        return {"error": "bad_order", "detail": "width_in and height_in must be > 0, quantity >= 1"}
    try:
        get_spec(product_id)
    except UnknownProductError as exc:
        return {"error": "unknown_product", "detail": str(exc)}
    target = _resolve(path)
    if isinstance(target, dict):
        return target
    return target, _order(product_id, width_in, height_in, quantity)


@mcp.tool(annotations=READ_ONLY)
def list_products() -> dict[str, Any]:
    """Products this checker knows, with their ids and key print requirements."""
    return {
        "products": [
            {
                "product_id": s.product_id,
                "name": s.display_name,
                "min_dpi": s.min_dpi,
                "bleed_in": s.bleed_in,
                "allows_transparency": s.allows_transparency,
            }
            for s in all_specs()
        ]
    }


@mcp.tool(annotations=READ_ONLY)
def get_product_spec(product_id: str) -> dict[str, Any]:
    """Every print requirement for one product: DPI, bleed, safe zone, minimum text size,
    minimum stroke, minimum contrast, colour modes, transparency, aspect tolerance."""
    return tool_get_product_spec(product_id)


@mcp.tool(annotations=READ_ONLY)
def check_artwork(
    path: str, product_id: str, width_in: float, height_in: float, quantity: int = 1
) -> dict[str, Any]:
    """Check one artwork file for an order, with the shipped pipeline (no model, $0).

    Returns the verdict (APPROVE, REQUEST_FIX or ESCALATE), the issues behind it with
    their measurements, and the message to send the customer when a fix is needed.
    `path` is relative to the server's root or absolute inside it; sizes are the ordered
    finished size in inches, without bleed.
    """
    prepared = _prepare(path, product_id, width_in, height_in, quantity)
    if isinstance(prepared, dict):
        return prepared
    target, order = prepared
    model = _model()
    try:
        verdict, p_defect, issues, _ = decide_explained(
            PreflightCase(case_id="mcp", image_path=target, order=order), model, model.threshold
        )
    except Exception as exc:  # noqa: BLE001 - a failure is a payload, never a crash
        return {"error": "check_failed", "detail": f"{type(exc).__name__}: {exc}"}
    shown = list(verdict.issues) or [i for i in issues if i.severity is Severity.ADVISORY]
    return {
        "verdict": verdict.verdict.value,
        "escalation_reason": verdict.escalation_reason.value if verdict.escalation_reason else None,
        "issues": [_issue(i) for i in shown],
        "customer_message": verdict.customer_message,
        "p_defect": p_defect,
        "cost_usd": 0.0,
    }


@mcp.tool(annotations=READ_ONLY)
def inspect_file(
    path: str, product_id: str, width_in: float, height_in: float, quantity: int = 1
) -> dict[str, Any]:
    """Exact metadata measurements: colour mode, pixel size, declared and effective DPI,
    bleed present, aspect ratio, alpha channel, and the issues they prove."""
    prepared = _prepare(path, product_id, width_in, height_in, quantity)
    if isinstance(prepared, dict):
        return prepared
    target, order = prepared
    try:
        return tool_inspect_file(image_path=target, order=order)
    except Exception as exc:  # noqa: BLE001
        return {"error": "tool_failed", "detail": f"{type(exc).__name__}: {exc}"}


@mcp.tool(annotations=READ_ONLY)
def analyse_pixels(
    path: str, product_id: str, width_in: float, height_in: float, quantity: int = 1
) -> dict[str, Any]:
    """Exact pixel measurements: thinnest stroke, lowest contrast, transparency, smallest
    text size, and ink inside the keep-out margin on each edge, with the issues they prove."""
    prepared = _prepare(path, product_id, width_in, height_in, quantity)
    if isinstance(prepared, dict):
        return prepared
    target, order = prepared
    try:
        return tool_analyse_pixels(image_path=target, order=order)
    except Exception as exc:  # noqa: BLE001
        return {"error": "tool_failed", "detail": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
