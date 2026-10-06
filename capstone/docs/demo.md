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

## Lettering review page (`/lettering`)

The rule-loop lettering rounds (rule-loop.md §5) as a page: for each region a person
judged, the whole sticker with the region boxed, a readable close-up, the person's
answer(s), and what the shipped pipeline does with the same file today.

- **System confidence is a measurement**: the text height as a share of the minimum, and
  the gap in pixels at print resolution. The fixed 0.95 on rule verdicts is never shown;
  it was never calibrated.
- **Person confidence is consistency**: controls right (17/20 in round 1, where captions
  were shown too small; 20/20 in round 1b) and repeats answered the same (9/15).
- **Groups**: agree (real text, too small), policy question (garbled lettering), system
  false positive (not text), unresolved (can't tell), flipped (a repeat that changed),
  control.
- The gallery picks a fixed number per group, most legible first; every region is in
  the table below it. The page says plainly that no rule was built from these labels.

```bash
python -m capstone.demo.build_lettering --rebuild   # fetches ~150 DiffusionDB images
```

`--rebuild` recreates only the labelled ai_art_v1 cases from their sealed seeds and stops
unless each matches its sealed manifest row; every image must also match the size
recorded with its label. Outputs `static/lettering.json` and `samples/lettering/`, both
committed. The four round-1b controls from ai_art_v2 are counted, not shown.

## What the numbers are (round 4, scored once at 090477c, code frozen at 98ecc30)

| Sealed set | rules_only | shipped (CV decider) | exact 95% bound |
|---|---|---|---|
| real_art_v5: 1,000 unseen real illustrations | 73.9% / 3.9% | **78.7% / 0.0%** (0 of 500) | **0.6%** |
| mistakes_v2: 480 real-art stickers through customer processes | 18.8% / 0.0% | **87.0% / 0.0%** (0 of 240) | 1.2% |

## Hosted: Streamlit Community Cloud (free)

`capstone/demo/streamlit_app.py` is the same check as a Streamlit page, for free hosting.
It calls the same function as the FastAPI app (`pipeline.check_file`), so a file gets the
same verdict on both; `tests/test_streamlit_app.py` checks every sample through
Streamlit's own test runner. It has the samples and an upload, and no AI generator: the
hosted page holds no key. Uploads are capped at 40 MB (`.streamlit/config.toml`) and
live in a temporary directory for the length of the check.

Why this host: Hugging Face Spaces showed as paid on the owner's account, and Render's
free 512 MB is too close to the measured peak. Measured 2026-09-27 on Python 3.13 with only
`capstone/demo/requirements.txt` installed: 709 MB of packages, **506 MB peak memory**
over all 9 samples in one process, 1-5 s a check on this machine.

Deploy (once):

1. Sign in at share.streamlit.io with the GitHub account that can see this repo, and
   allow access to it.
2. **Create app** → deploy from GitHub. Repository `ashvolt/artwork-preflight-agent`, branch `main`, main
   file path `capstone/demo/streamlit_app.py`.
3. **Advanced settings** → Python **3.13**. No secrets. Save, then **Deploy**.
4. The first build installs `capstone/demo/requirements.txt` and the apt packages in the
   root `packages.txt` (OpenCV's system libraries), a few minutes. Every push to `main`
   redeploys.

**`packages.txt` must stay at the repo root.** rapidocr requires the full
`opencv-python`, which shares the `cv2` directory with the headless build we list;
whichever installs last owns it. The full build links `libGL.so.1` and `libglib-2.0`. The
first deploy (2026-09-27) had the file in `capstone/demo/`, where it was not picked up,
and failed at `import cv2`.

The requirements file sits next to the app so Community Cloud uses it instead of the
root `pyproject.toml` (the whole dev and data stack). Keep its pins in step with
`pyproject.toml`, especially `rapidocr_onnxruntime<1.3`. An app with no visitors for a
while sleeps; the first visit after that wakes it, which takes about a minute.

The FastAPI app still runs locally (above) and still suits any Python container host.
Before keeping uploads anywhere, the page would need consent wording.

## Not in the demo yet

- AI-generated images are in: two sealed DiffusionDB sets on the results page, and the
  live generator card now runs against Cloudflare Workers AI (FLUX.1-schnell; the
  environment's CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN). Measured 2026-09-26 in
  ai-art.md §10. On stage, expect most generated stickers to be sent back for hairline
  detail, and the provider's safety filter to refuse some harmless prompts (the page
  says so plainly). FAKE_TRANSPARENCY misses AI-painted checkerboards (ai-art.md §5a),
  though FLUX did not paint any in 10 of 10 tries.
- Real customer uploads.
