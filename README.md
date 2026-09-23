# Agents: Level 0 to Pro

Learning agent engineering by building. Six taught levels, then one production-ready agent
that solves a real operations problem.

**Plan:** [PLAN.md](PLAN.md) — schedule, capstone criteria, budget, what "production-ready"
has to mean here.

> **Unofficial project.** This repository is not affiliated with, endorsed by, or connected
> to Sticker Mule. It uses no Sticker Mule data, systems, or assets. Every dataset here is
> synthetic and generated locally. The company is named only to describe the class of
> operations problem the capstone addresses.

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

cp .env.example .env               # then put your real key in .env
```

`.env` is gitignored. Never commit a key, never paste one into source or a notebook cell.
A leaked `sk-ant-...` in public git history is live until you rotate it — rewriting history
does not un-leak it.

## Running

```bash
pytest -m "not integration"        # free: pure logic only, no API calls
pytest                             # includes integration tests, costs money
ruff check .
```

Integration tests are marked and deselected by default. Run them deliberately.

---

## Levels

| Level | Topic | Status |
|-------|-------|--------|
| [L0](levels/L0_raw_api/) | Raw API: messages, params, stop reasons, token accounting, cost | in progress |
| L1-L8 | Tool loops, structured output, state, patterns, evals, MCP, production | folded into the capstone |
| [Capstone](capstone/docs/brief.md) | Artwork preflight triage — auto-approve clean files, escalate the rest | **[RESULTS](capstone/docs/results.md)** — both criteria met on a held-out split |

Each level folder holds `README.md` (the concept), `exercise.py` (stubs I fill in),
`test_exercise.py` (the bar), and `NOTES.md` (what I got wrong, in my own words).

## Learning log

| Date | Entry |
|------|-------|
| 2026-09-20 | Plan written, repo scaffolded, L0 started |
| 2026-09-20 | Ladder abandoned for the deadline. L1-L8 now learned inside the capstone, driven by evals. |
| 2026-09-20 | Capstone picked: artwork preflight triage. Brief written, gate cleared. |
| 2026-09-21 | Architecture doc + editable excalidraw diagram written. Local env set up; L0 in progress. |
| 2026-09-22 | Spec-kit docs, schemas, generator, deterministic checks, eval harness, agent. First baselines. |
| 2026-09-23 | **Holdout scored once: 82.0% auto-approve, 0 false approves, both arms.** Tool loop measured as worse AND costlier than a single call. Model contributes one extra detection per 312 files. See [results.md](capstone/docs/results.md). |
