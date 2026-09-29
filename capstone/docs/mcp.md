# MCP server — the checker as tools for any MCP client

**Date:** 2026-09-27 · **Code:** `capstone/mcp_server.py` · **Tests:** `capstone/tests/test_mcp_server.py`
· **API spend:** $0.00 (no model is called)

> **Unofficial project.** Not affiliated with Sticker Mule.

PLAN.md day 8 (L6): expose the pipeline's tools over MCP, so an assistant can check a
file with the same code that ships, instead of judging print readiness by eye.

## Tools

| Tool | What it returns |
|---|---|
| `list_products` | Product ids with minimum DPI, bleed, and whether transparency is allowed |
| `get_product_spec(product_id)` | Every requirement for one product |
| `check_artwork(path, product_id, width_in, height_in, quantity=1)` | The shipped CV decider's verdict (APPROVE / REQUEST_FIX / ESCALATE), the issues with their measurements, and the customer message |
| `inspect_file(...)` | Exact metadata measurements (colour mode, pixels, effective DPI, bleed, aspect, alpha) |
| `analyse_pixels(...)` | Exact pixel measurements (stroke, contrast, transparency, text size, margin ink) |

All five are marked read-only. The agent-internal `submit_verdict` is not exposed. Sizes
are the ordered finished size in inches, without bleed.

## Run it

```bash
pip install -e ".[mcp]"
PREFLIGHT_MCP_ROOT=/path/to/artwork python -m capstone.mcp_server    # stdio
```

An MCP client that launches stdio servers from a JSON config takes:

```json
{
  "mcpServers": {
    "artwork-preflight": {
      "command": "python",
      "args": ["-m", "capstone.mcp_server"],
      "env": { "PREFLIGHT_MCP_ROOT": "/path/to/artwork" }
    }
  }
}
```

Run it from the repo root with the environment where the project is installed (or give
the full path to that environment's `python`).

## Boundaries

- **Files are read only under `PREFLIGHT_MCP_ROOT`** (default: the directory the server
  started in). Absolute paths outside it, `../` traversal, and symlinks that resolve
  outside it return `{"error": "outside_root"}` and are never opened. Files over 40 MB are
  refused, the demo's cap.
- **What the artwork says never reaches the caller.** The text detector finds where text
  is and how large; its recogniser never runs. Instructions written into an image cannot
  travel through this server to the model on the other side (red team RT01/RT02).
- **Tools never raise.** An unknown product, a bad size, a missing file, or a crash
  inside a check comes back as `{"error": ..., "detail": ...}`. An unreadable file comes
  back as a normal ESCALATE verdict with `UNREADABLE_FILE`, the pipeline's own behaviour.

## Checked

- Tests go through a real MCP client connected in-process: the tool list and read-only
  annotations, every demo sample's verdict against the decider called directly,
  measurement payloads, three path escapes, a symlink out of the root, and bad input.
  17 tests.
- Run as a separate process over stdio and called by an MCP client: same tools, same
  verdicts.
- `mcp` 2.2.0 (the server class is `MCPServer`; 1.x called it `FastMCP`). CI installs the
  extra, so the tests run there and are not skipped.
