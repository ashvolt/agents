# Product walkthrough video

**Date:** 2026-10-06 · **Output:** `out/stickermule-preflight-demo.mp4` (13:10, 1920×1080,
h264 + AAC) with `out/stickermule-preflight-demo.srt` · **API spend:** $0.00 · **Network:**
none at build time.

> **Unofficial project.** Not affiliated with, endorsed by, or connected to Sticker Mule.
> No Sticker Mule data, systems or assets are used. Volume and cost figures are labelled
> assumptions.

A narrated product walkthrough for a mixed audience — operations stakeholders and the
technical team. It explains what the product does, the problem it removes, how it decides,
what it was measured at, what it costs, how it integrates, and where it stops.

It is one continuous screen recording of the running app. Playwright drives the real
FastAPI demo — clicking samples, waiting for genuine verdicts, zooming on the measurement
that failed, and driving the upload form — and the explanatory sections are a local HTML
deck ([cards.html](cards.html)) the same browser navigates to, so nothing is a montage of
stills.

## What the video covers

| Segment | Shown with |
|---|---|
| The problem and its cost | card |
| Why the target is "clear as much as possible under a ceiling on wrong clearances" | card |
| The two inputs: artwork and order context | live app |
| **APPROVE** — a print-ready file clears, ~1 s, $0 | live check |
| Advisory finding vs blocking finding | live check (chat-app file) |
| **REQUEST_FIX** with cited measurements and a drafted customer reply | live check (screenshot file) |
| Marked-up artwork: cut line, safe zone, problem regions | live app |
| Pixel-level checks: unintended transparency | live check (background-remover file) |
| Spec-driven checks: missing bleed | live check (trim-size export) |
| **ESCALATE** — unsupported file, never guessed | live check (animated GIF) |
| Same file, different product: bleed, hairlines and text minimums change | live upload (banner at 24×14 in) |
| The three kinds of check, and why sorting them matters | card |
| The three outcomes and the operating boundaries | card |
| Published validation: sealed rounds, scored once | live app |
| What the evaluation settled, including where a model did not pay | card |
| Cost per file against the model alternatives | live app |
| The business case | card |
| The hardest check, and how human labels are treated | live app (lettering review) |
| Integration: HTTP service, review UI, MCP tool, hosting, logging | card |
| Limits and out-of-scope | card |
| Rollout: shadow mode, measure, then switch on per product | card |

## Pieces

| File | What it is |
|---|---|
| [script.py](script.py) | The whole script: 24 scenes, each with its browser actions and narration. The only file carrying copy. |
| [cards.html](cards.html) | The explanatory cards, as a web page the recorder navigates. |
| [record.py](record.py) | Synthesises the voice, drives Chromium on that clock, records it, writes `out/timeline.json`. |
| [assemble.py](assemble.py) | Places the narration against the measured timeline, writes the `.srt`, encodes the mp4. |

## Build it

```bash
pip install -r ../requirements.txt
pip install playwright kokoro-onnx soundfile imageio-ffmpeg
python -m playwright install chromium

# voice model (~350 MB, once) into models/
#   kokoro-v1.0.onnx and voices-v1.0.bin from
#   github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0

python -m uvicorn capstone.demo.app:app --port 8111 &   # from the repo root
python record.py        # ~13 min: it records in real time
python assemble.py      # -> out/stickermule-preflight-demo.mp4 + .srt
```

Everything is local and open source. **Kokoro-82M** (Apache-2.0) does the voice on CPU, so
there is no API key, no network call and no third-party service that sees the script.
Playwright is Apache-2.0; ffmpeg arrives through `imageio-ffmpeg`.

## How the voice stays in sync

Recording cannot be paused, so the order matters:

1. Every scene's narration is synthesised first and measured.
2. The browser is driven on that clock: it performs the scene's actions, then holds the
   frame for whatever is left of that scene's audio.
3. `record.py` writes the **measured** start and end of each scene to `out/timeline.json`.
4. `assemble.py` places each clip at its measured time, so a slow check or a slow page
   cannot push the voice out of step with the picture. Where a scene's actions ran past its
   narration, the voice starts after them and the run prints the overrun.

Subtitles come from the same narration, split per sentence and merged so no cue flashes by
faster than ~1.4 s. They ship as a sidecar `.srt` with the same stem as the mp4, so most
players pick them up automatically — keep the two files together.

## Changing it

Edit the narration or actions in `script.py` and re-run both steps. `record.py` reuses
`out/voice/*.wav` for scenes whose audio already exists, so delete that folder after a copy
change. Roughly 180 words of narration costs a minute of video.

Numbers spoken in the script are drawn from [brief.md](../../docs/brief.md),
[results.md](../../docs/results.md), [decider.md](../../docs/decider.md) and
[demo.md](../../docs/demo.md), and the live segments show whatever the app actually returns
— if a verdict changes, the narration has to be updated to match.

## Known rough edges

- `starlette` must be `<0.42` for the pinned FastAPI 0.110, or the app fails at import with
  `Router.__init__() got an unexpected keyword argument 'on_startup'`.
- The `overlay` scene's zoom lands shallower than the 1.5 it asks for. The artwork is
  centred and readable and the narration matches, but the close-up is weaker than the other
  zooms; the transform is applied to `document.body`, which interacts badly with that
  scene's preceding scroll.
- The voice is synthetic. Re-record over the same picture if a human voice matters for a
  given audience.
- `out/` and `models/` are gitignored: the mp4 is ~60 MB and the voice model ~350 MB. The
  scripts and the card deck are committed, so a checkout rebuilds the video.
