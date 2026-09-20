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
| L1 | The tool loop, written by hand | not started |
| L2 | Structured output and failure handling | not started |
| L3 | A single real agent with state and tools | not started |
| L4 | Patterns: routing, ReAct, reflection, planner-executor | not started |
| L5 | Evals: datasets, judges, regression gates | not started |
| L6-L8 | MCP, multi-agent, production — folded into the capstone | not started |
| Capstone | Production agent for a print-ops problem | not started |

Each level folder holds `README.md` (the concept), `exercise.py` (stubs I fill in),
`test_exercise.py` (the bar), and `NOTES.md` (what I got wrong, in my own words).

## Learning log

| Date | Entry |
|------|-------|
| 2026-09-20 | Plan written, repo scaffolded, L0 started |
