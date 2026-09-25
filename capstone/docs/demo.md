# Demo — the checker in a browser

**Date:** 2026-09-24 · **Code:** `capstone/demo/` · **API spend:** $0.00

> **Unofficial project.** Not affiliated with Sticker Mule.

A local web app over the shipped pipeline (the CV decider: rules, OpenCV features, a JSON
logistic model, no model call). Upload artwork and pick a product: the page shows the
verdict, every issue with its measurement, the drafted customer message, and the artwork
with the cut line, safe zone and problem regions drawn on it. A results page shows every
sealed evaluation, the customer-mistake gallery, the cost comparison and the limits.

## Run it

```bash
pip install -e ".[dev,demo]"
uvicorn capstone.demo.app:app --port 8000
# open http://localhost:8000
```

Nothing is stored: an upload lives in a temporary directory for the length of the request.

## Record the walkthrough

```bash
uvicorn capstone.demo.app:app --port 8000 &
NODE_PATH=$(npm root -g) node capstone/demo/walkthrough/record.mjs http://localhost:8000
# -> capstone/demo/walkthrough/out/walkthrough.webm  (gitignored)
```

Playwright drives the page in Chromium and records a captioned video. Re-run it after any
change; the video cannot drift from the product. Add a voice-over on top if wanted.

## Rebuild the report data

```bash
python -m capstone.demo.build_reports --mistakes-run <cv_decider run stem>
```

Reads named run files from `capstone/evals/runs/` and writes `static/reports.json` and
`samples/`. Both are committed, so the demo runs without the run files. Every number on
the results page comes from a run listed in `build_reports.ROUNDS`; the only typed-in
figures are the cost assumptions, which say where they come from.

Run files are gitignored. Where a round's run files are missing (a fresh checkout), its
row is carried over unchanged from the committed `reports.json`, and the build prints
which rows it carried. Omitting `--mistakes-run` keeps the gallery and samples as they
are. The AI-art rounds (2026-09-25) were added this way; the older rows are
byte-identical.

Gallery examples: for each customer process, the largest correctly decided file,
preferring a defective one where the process produces defects. Size is chosen for
legibility; the verdict is not a criterion, and the counts sit next to each example.

## What the numbers are (round 4, scored once at 090477c, code frozen at 98ecc30)

| Sealed set | rules_only | shipped (CV decider) | exact 95% bound |
|---|---|---|---|
| real_art_v5: 1,000 unseen real illustrations | 73.9% / 3.9% | **78.7% / 0.0%** (0 of 500) | **0.6%** |
| mistakes_v2: 480 real-art stickers through customer processes | 18.8% / 0.0% | **87.0% / 0.0%** (0 of 240) | 1.2% |

## Hosting later

The app is a single FastAPI process with no state and no secrets. Anything that runs a
Python container works (Hugging Face Spaces, Render, Fly.io). Two things to settle first:
an upload size limit at the proxy (the app caps at 40 MB), and whether uploads may be kept
(today they are not; keeping them would need consent wording on the page).

## Not in the demo yet

- AI-generated images. DiffusionDB is now reachable and scored as a sealed set
  (ai-art.md: 54.3% / 0 of 339 wrong approvals). The live generator card still has not
  run against a real provider (no key here). FAKE_TRANSPARENCY no longer false-alarms
  (0 of 1,000 fresh AI images), but it also misses real AI-painted checkerboards
  (ai-art.md §5a). Do not present it as catching them.
- Real customer uploads.
