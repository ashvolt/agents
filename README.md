# Artwork Preflight

**Automated print-readiness checking for custom print orders.** Every uploaded file is
measured against the product it was ordered on, in about a second, on CPU, with no model call
— and comes back cleared for production, returned to the customer with an explanation, or
escalated to a person with the findings attached.

> **Unofficial project.** Not affiliated with, endorsed by, or connected to Sticker Mule. It
> uses no Sticker Mule data, systems or assets. Every dataset here is synthetic or built from
> openly licensed artwork. The company is named only to describe the class of operations
> problem this addresses. Volume and cost figures are labelled assumptions.

---

## The problem

A custom print shop takes uploaded artwork and prints it on a physical product. Before
anything reaches a press, a person has to confirm the file will actually print: enough
resolution at the size ordered, bleed where the blade cuts, the right colour mode, no text
small enough to turn to mud, nothing important sitting in the trim margin.

That review protects the print and scales one-for-one with orders, which makes it the largest
variable cost in the pipeline. Most files are already fine, so most of that time is spent
confirming nothing is wrong.

```
customer uploads art ──▶ human reviews & fixes ──▶ proof ──▶ approval ──▶ printed ──▶ shipped
                              ▲
                              └── most of this time is spent on files that were already fine
```

On the modelling assumptions in [brief.md](capstone/docs/brief.md) — 4,000 files/day, 25%
defective, 40 s to clear a clean file, $28/hr loaded — clean files alone consume **33
reviewer-hours a day, around $340,000 a year**.

**The goal is not to replace the reviewer.** It is to stop the clean files reaching them, and
to make the broken ones arrive already diagnosed.

## What "good" has to mean

Accuracy is the wrong target, because the two ways of being wrong cost very different amounts.
A file wrongly cleared becomes a bad print: reprint, reship, a support ticket, and a customer
who stops trusting the proof — roughly $18 every time. At a 1% rate that eats over half the
saving; at 5% the system destroys more value than it creates.

> **The metric: auto-approval rate, subject to a false-approve rate at or below 1%.**

The release gate fails any build that buys coverage by raising that ceiling. Quick to
escalate, slow to approve.

## Measured results

Sealed sets, scored once, reproducible from a manifest and a seed. Full detail in
[results.md](capstone/docs/results.md) and [real-art.md](capstone/docs/real-art.md).

| Sealed set | Auto-approve | Wrong clearances | Exact 95% bound |
|---|---|---|---|
| 1,000 unseen real illustrations | **78.7%** | **0 of 500** | 0.6% |
| 480 files damaged by real customer processes | **87.0%** | **0 of 240** | 1.2% |
| 1,000 AI-generated sticker images (DiffusionDB) | 57.1% | 0 of 354 | 0.8% |

Cost per file at inference: **$0.0000** — the shipped decider is computer vision plus a small
logistic model, with no model call. A vision model on every file was measured at $0.0066
(single call) or $0.0117 (tool loop), and is not used; see
[decider.md](capstone/docs/decider.md) for why that changed.

## How it decides

Checks fall into three kinds, and sorting them correctly is the core of the design.

| Kind | Checks | Cost |
|---|---|---|
| **1 · file metadata** — exact arithmetic | `LOW_RESOLUTION` `MISSING_BLEED` `WRONG_COLOR_MODE` `ASPECT_MISMATCH` `UNREADABLE_FILE` | microseconds, $0 |
| **2 · pixel analysis** — measured, not guessed | `THIN_LINES` `LOW_CONTRAST` `UNINTENDED_TRANSPARENCY` `TEXT_TOO_SMALL` | CPU, $0 |
| **3 · judgement** — genuinely ambiguous | ink inside the cut margin: deliberate bleed, or a logo losing its top third? | the only place a model was ever justified |

Three outcomes, of which **only one is automatic**:

| Verdict | Who acts | When |
|---|---|---|
| `APPROVE` | Nobody — straight to production | No blocking issue, and every check that ran is one the system is trusted to make alone |
| `REQUEST_FIX` | Customer, from a drafted message a reviewer approves in one click | A deterministic check failed with a concrete, citable measurement |
| `ESCALATE` | A person, with the findings attached | Low confidence, conflicting signals, unsupported file, a detector that returned nothing, or a blown budget |

Operating boundaries: it never edits artwork, never contacts a customer on its own, and never
silently clears a file. Uploaded files are treated strictly as data — text rendered inside an
image is described, never followed as an instruction.

## Diagrams and technical detail

- [architecture.md](capstone/docs/architecture.md) — the as-built engineering picture, with
  sequence and component diagrams, and the designs that measurement killed
- [architecture.excalidraw](capstone/docs/architecture.excalidraw) — editable source; open at
  [excalidraw.com](https://excalidraw.com) via *Open → load from file*
- [specs/001-artwork-preflight-triage/](specs/001-artwork-preflight-triage/) — spec, plan,
  research, data model and task breakdown

## Quickstart

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); .venv/bin/activate on macOS/Linux
pip install -e ".[demo]"

python -m uvicorn capstone.demo.app:app --port 8000
# open http://localhost:8000
```

Drop in artwork, pick the product and the ordered size, and the page shows the verdict, every
issue with its measurement, the drafted customer message, and the artwork with the cut line,
safe zone and problem regions drawn on it. Nothing is stored: an upload lives in a temporary
directory for the length of one request.

No API key is needed to run the checker. `.env` is only required for the optional model
experiments and the AI-art generator; it is gitignored, and a leaked key must be rotated
rather than rebased away.

### As a library

```python
from pathlib import Path
from capstone.demo.pipeline import check_file

result = check_file(Path("artwork.png"), "die-cut-sticker", width_in=3, height_in=3)
result["verdict"]            # APPROVE | REQUEST_FIX | ESCALATE
result["issues"]             # each with code, severity, message, evidence
result["customer_message"]   # drafted reply, or None
```

### As an MCP tool

```bash
pip install -e ".[mcp]"
PREFLIGHT_MCP_ROOT=/path/to/artwork python -m capstone.mcp_server   # stdio
```

Five read-only tools — `list_products`, `get_product_spec`, `check_artwork`, `inspect_file`,
`analyse_pixels` — so an existing support or operations agent can call the same checker.
Details and client config in [mcp.md](capstone/docs/mcp.md).

### Hosted page

`capstone/demo/streamlit_app.py` is the same check as a Streamlit page for free hosting, and
calls the same function as the web app, so a file gets the same verdict either way. Deployment
notes in [demo.md](capstone/docs/demo.md).

## Product configuration

Requirements — minimum DPI, bleed, safe zone, minimum text size, whether transparency is
allowed — live per product in `capstone/src/product_specs.py`. Adding a product is a row in
that table, not new code. The same file passes on one product and fails on another; that is
the point, and the video walkthrough shows it.

## Video walkthrough

A 13-minute narrated product walkthrough, recorded against the running app: the three
outcomes, cited measurements, the drafted customer reply, the marked-up artwork, and the same
file re-checked as a large banner. Build it with
[capstone/demo/video/](capstone/demo/video/README.md) — the voice is synthesised locally, so
it needs no API key and no network.

The web app also serves a silent captioned walkthrough at `/walkthrough.webm` when one has
been recorded into `capstone/demo/walkthrough/out/`. Both recordings are gitignored build
artefacts and rebuild from a checkout.

## Repository layout

```
capstone/
  src/          pipeline, deciders, schemas, product specs, prompts
  tools/        bucket 1 (metadata) and bucket 2 (pixel) checks
  data/         dataset builders: synthetic art, real illustrations, customer mistakes, AI art
  evals/        harness, gates, baselines, red-team and decider training
  demo/         FastAPI app, Streamlit page, report builders, video walkthrough
  ops/          budgets, pricing, review queue, tracing
  docs/         everything below
  mcp_server.py MCP tools over the shipped pipeline
specs/          spec-kit documents for the build
```

## Tests

```bash
pytest -m "not integration"   # free: pure logic only, no API calls
pytest                        # includes integration tests, which cost money
ruff check .
```

Anything that hits a real API is marked `integration` and deselected by default. Logic that
matters is testable without the network.

## Documentation

| Document | What it covers |
|---|---|
| [results.md](capstone/docs/results.md) | What was measured, on what, and what it means |
| [decider.md](capstone/docs/decider.md) | The shipped decider: OpenCV features plus a logistic model, validated on 1,000 fresh cases |
| [real-art.md](capstone/docs/real-art.md) | Real illustrations as uploads: four sealed rounds, 6.2% → 0 of 500 wrong clearances |
| [ai-art.md](capstone/docs/ai-art.md) | AI-generated artwork: two sealed sets, 0 wrong clearances, auto-approve under target |
| [rule-loop.md](capstone/docs/rule-loop.md) | How rules change: a model proposes, a person decides, a fresh sealed set judges |
| [brief.md](capstone/docs/brief.md) | The business case: problem, ROI model, failure costs, human-in-the-loop policy |
| [architecture.md](capstone/docs/architecture.md) | As-built engineering picture and diagrams |
| [mcp.md](capstone/docs/mcp.md) | The MCP server and its tools |
| [demo.md](capstone/docs/demo.md) | The web app, results page, mistake gallery and hosting |
| [limits.md](capstone/docs/limits.md) | Sixteen things this system cannot do, most found by measuring |
| [runbook.md](capstone/docs/runbook.md) | How to run it, what breaks, and what pages you |
| [scene-narration.md](capstone/docs/scene-narration.md) | Parked spike: describing and auto-fixing artwork, and why it is out of scope |

## Limits

Named here rather than buried, because a checking tool that hides its blind spots gets trusted
exactly where it should not be. Full list in [limits.md](capstone/docs/limits.md).

- **Not tested on real customer uploads.** Real files carry colour profiles, layers,
  vector-raster mixes and fonts that do not render as expected.
- **Known misses:** grey artwork the text detector reads as text, near-white art on white
  stock, a hairline inside thick ink, low-resolution art upscaled to look sharp.
- **Cost per file is unproven at production image sizes** — demo artwork is roughly a quarter
  the pixels of a real upload.
- **Zero wrong clearances in 500 is not a zero rate.** The exact 95% upper bound is 0.6% —
  inside the ceiling, but not zero.
- **No adversarial test suite yet**, so injection resistance is designed for and unmeasured.
- **Out of scope on purpose:** fixing artwork, generating proofs, IP and content screening,
  pricing, and anything downstream of approval.

## Roadmap

1. Run it in shadow mode beside a live queue — same files, no automatic clearance — and
   compare verdicts against what reviewers decided.
2. Measure the false-clearance rate on real uploads: the one number the ceiling depends on,
   and the one the current datasets cannot settle.
3. Build the adversarial suite.
4. Switch automatic clearance on per product type, where the measured rate supports it.

## License

[MIT](LICENSE).
