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

## Capstone documents

| Document | What it is |
|---|---|
| [results.md](capstone/docs/results.md) | **Start here.** What was measured, on what, and what it means |
| [decider.md](capstone/docs/decider.md) | **Update.** The vision model removed: OpenCV features + a logistic decider, validated on 1,000 fresh cases |
| [scene-narration.md](capstone/docs/scene-narration.md) | **Parked (out of scope, brief §11).** Engine describes the image, verifies the description by redrawing it, auto-fixes and re-checks proofs; a small model only narrates, and every number it writes is checked |
| [real-art.md](capstone/docs/real-art.md) | **Reality check.** 450 real illustrations as sticker uploads: what broke, what was fixed, and the unseen-art score (6.2% false-approve — not yet shippable) |
| [brief.md](capstone/docs/brief.md) | The business case: problem, ROI, failure costs, HITL policy |
| [architecture.md](capstone/docs/architecture.md) | As-built engineering picture, with the designs that measurement killed |
| [limits.md](capstone/docs/limits.md) | Fourteen things this system cannot do, most found by measuring |
| [runbook.md](capstone/docs/runbook.md) | How to run it, what breaks, what pages you |
| [walkthrough.md](capstone/docs/walkthrough.md) | Reading order and question bank |
| [specs/001-…](specs/001-artwork-preflight-triage/) | Spec-kit: constitution, spec, plan, research, data model, tasks |

**Headline:** on a held-out split scored once — 82.0% auto-approve, 0 false approves in 41
approvals, both criteria met. The deterministic pipeline passes *without* the model; the
model's entire measured contribution is a 3.4 pp reduction in escalation rate. Total API
spend for the project: ~$4.30.

## Learning log

| Date | Entry |
|------|-------|
| 2026-09-20 | Plan written, repo scaffolded, L0 started |
| 2026-09-20 | Ladder abandoned for the deadline. L1-L8 now learned inside the capstone, driven by evals. |
| 2026-09-20 | Capstone picked: artwork preflight triage. Brief written, gate cleared. |
| 2026-09-21 | Architecture doc + editable excalidraw diagram written. Local env set up; L0 in progress. |
| 2026-09-22 | Spec-kit docs, schemas, generator, deterministic checks, eval harness, agent. First baselines. |
| 2026-09-23 | **Holdout scored once: 82.0% auto-approve, 0 false approves, both arms.** Tool loop measured as worse AND costlier than a single call. Model contributes one extra detection per 312 files. See [results.md](capstone/docs/results.md). |
| 2026-09-23 | **Vision model removed.** OpenCV margin features + a 5-feature logistic decider: 84.4% / 0.0% and 84.6% / 0.5% on two fresh sealed sets, $0/file. The model-free rules breach SC-002 at n=600 (2.4%) — the earlier pass was luck. See [decider.md](capstone/docs/decider.md). |
| 2026-09-23 | **Scene documents + verified fixes (spike).** Redraw-from-text fidelity 0.975 median; 23-31% of rejected files become print-ready proofs with no human; every proof re-verified and structure-checked; narration claim-checked. Live model run blocked: no API key here. See [scene-narration.md](capstone/docs/scene-narration.md). |
| 2026-09-24 | **Real artwork.** OpenMoji/Twemoji/Noto as sticker uploads; the synthetic results did not transfer (7.8% false-approve). DBNet text detection, line-level contrast, stroke-mask fixes and a cut-line guard: synthetic holdout 86.7% / 0.0%, unseen real art 78.1% / 6.2%. Local-model narration via Ollama built and tested; blocked here by network policy. See [real-art.md](capstone/docs/real-art.md). |
