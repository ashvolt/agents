"""Record the narrated walkthrough.

1. Synthesise every scene's narration locally with Kokoro-82M (ONNX) and measure it.
2. Drive the real app in Chromium on that clock, recording one continuous video.
3. Write timeline.json with each scene's measured start and end, so assemble.py can lay
   the audio down against what actually happened rather than what was planned.

Usage:
    python -m uvicorn capstone.demo.app:app --port 8111 &
    python record.py [--base http://127.0.0.1:8111] [--out out/]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import wave

from playwright.sync_api import sync_playwright

from script import SCENES

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS = "file:///" + os.path.join(HERE, "cards.html").replace("\\", "/")
SAMPLES = os.path.join(HERE, "..", "samples")
MODEL = os.path.join(HERE, "models", "kokoro-v1.0.onnx")
VOICES = os.path.join(HERE, "models", "voices-v1.0.bin")
VOICE = "am_michael"
SPEED = 1.2
TAIL = 1.0          # seconds of held frame after each scene's narration
LEAD = 1.6          # seconds before the first word

CURSOR_JS = """
(() => {
  if (window.__cursorReady) return;
  window.__cursorReady = true;
  const add = () => {
    // Runs at document_start, where documentElement may not exist yet.
    if (!document.documentElement || document.getElementById("__cursor")) return;
    const c = document.createElement("div");
    c.id = "__cursor";
    c.style.cssText = "position:fixed;z-index:2147483647;width:26px;height:26px;margin:-13px 0 0 -13px;"
      + "border-radius:50%;background:rgba(210,48,58,.22);border:2px solid #d2303a;pointer-events:none;"
      + "left:60%;top:70%;transition:left .55s cubic-bezier(.4,0,.2,1),top .55s cubic-bezier(.4,0,.2,1);";
    document.documentElement.appendChild(c);
    const s = document.createElement("style");
    s.textContent = "@keyframes __ping{from{transform:scale(1);opacity:.75}to{transform:scale(2.6);opacity:0}}";
    document.documentElement.appendChild(s);
  };
  window.__point = (x, y) => { add(); const c = document.getElementById("__cursor"); if (c) { c.style.left = x + "px"; c.style.top = y + "px"; } };
  window.__ping = () => {
    add();
    const c = document.getElementById("__cursor"); if (!c) return;
    const p = c.cloneNode(); p.id = ""; p.style.transition = "none";
    p.style.animation = "__ping .6s ease-out forwards";
    document.documentElement.appendChild(p); setTimeout(() => p.remove(), 650);
  };
  window.__zoom = (sel, s) => {
    const el = document.querySelector(sel); if (!el) return false;
    const r = el.getBoundingClientRect();
    const b = document.body;
    b.style.transition = "transform .85s cubic-bezier(.4,0,.2,1)";
    b.style.transformOrigin = (r.left + r.width / 2 + window.scrollX) + "px " + (r.top + r.height / 2 + window.scrollY) + "px";
    b.style.transform = "scale(" + s + ")";
    return true;
  };
  window.__unzoom = () => { const b = document.body; if (b) b.style.transform = "scale(1)"; };
  document.addEventListener("DOMContentLoaded", add);
  add();
})();
"""


def synth(out_dir: str) -> list[tuple[str, str, float]]:
    """Render every scene's narration to wav. Returns (id, path, seconds)."""
    from kokoro_onnx import Kokoro
    import soundfile as sf

    kokoro = Kokoro(MODEL, VOICES)
    os.makedirs(out_dir, exist_ok=True)
    clips = []
    for sid, _actions, text in SCENES:
        path = os.path.join(out_dir, f"{sid}.wav")
        if not os.path.exists(path):
            audio, sr = kokoro.create(text, voice=VOICE, speed=SPEED, lang="en-us")
            sf.write(path, audio, sr)
        with wave.open(path) as w:
            secs = w.getnframes() / w.getframerate()
        clips.append((sid, path, secs))
        print(f"  voice {sid:10s} {secs:6.1f}s", flush=True)
    return clips


def run_action(page, action, base: str) -> None:
    kind = action[0]
    if kind == "card":
        page.goto(f"{CARDS}#{action[1]}")
        page.wait_for_timeout(250)
    elif kind == "app":
        page.goto(base + action[1])
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(400)
    elif kind == "sample":
        sel = f'button.sample[data-file="{action[1]}"]'
        page.wait_for_selector(sel)
        box = page.locator(sel).bounding_box()
        page.evaluate("([x,y])=>window.__point(x,y)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        page.wait_for_timeout(600)
        page.evaluate("window.__ping()")
        page.click(sel)
        page.wait_for_selector("#result .verdict", timeout=60000)
        page.wait_for_timeout(500)
    elif kind == "upload":
        _, name, product, width, height = action
        page.set_input_files("#file", os.path.join(SAMPLES, name))
        page.select_option("#product", product)
        page.fill("#w", str(width))
        page.fill("#h", str(height))
        box = page.locator("#go").bounding_box()
        page.evaluate("([x,y])=>window.__point(x,y)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        page.wait_for_timeout(600)
        page.evaluate("window.__ping()")
        page.click("#go")
        page.wait_for_selector("#result .verdict", timeout=60000)
        page.wait_for_timeout(500)
    elif kind == "zoom":
        # Settle any in-flight smooth scroll first: the zoom origin comes from the
        # element's rectangle, and a moving rectangle lands the zoom somewhere else.
        page.evaluate("(s)=>{const e=document.querySelector(s.split(',')[0].trim())||document.querySelector(s.split(',').pop().trim());"
                      "if(e) e.scrollIntoView({block:'center',behavior:'instant'});}", action[1])
        page.wait_for_timeout(450)
        for sel in action[1].split(","):
            if page.evaluate("([s,z])=>window.__zoom(s,z)", [sel.strip(), action[2]]):
                break
        page.wait_for_timeout(900)
    elif kind == "unzoom":
        page.evaluate("window.__unzoom()")
        page.wait_for_timeout(700)
    elif kind == "scroll":
        page.evaluate("(d)=>window.scrollBy({top:d,behavior:'smooth'})", action[1])
        page.wait_for_timeout(900)
    elif kind == "point":
        box = page.locator(action[1]).bounding_box()
        if box:
            page.evaluate("([x,y])=>window.__point(x,y)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        page.wait_for_timeout(500)
    elif kind == "wait":
        page.wait_for_timeout(int(action[1] * 1000))
    else:
        raise ValueError(f"unknown action {kind}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8111")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print("synthesising narration (local, Kokoro-82M)...")
    clips = synth(os.path.join(args.out, "voice"))
    planned = sum(c[2] + TAIL for c in clips) + LEAD
    print(f"narration total {planned/60:.1f} min\nrecording...")

    timeline = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-device-scale-factor=1", "--hide-scrollbars"])
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=os.path.join(args.out, "raw"),
            record_video_size={"width": 1920, "height": 1080},
        )
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        t0 = time.perf_counter()
        page.goto(f"{CARDS}#title")
        page.wait_for_timeout(int(LEAD * 1000))

        for (sid, actions, _text), (_sid, _path, secs) in zip(SCENES, clips):
            start = time.perf_counter() - t0
            for action in actions:
                run_action(page, action, args.base)
            elapsed = (time.perf_counter() - t0) - start
            over = elapsed - secs
            if over < 0:
                page.wait_for_timeout(int((-over) * 1000))
            page.wait_for_timeout(int(TAIL * 1000))
            end = time.perf_counter() - t0
            timeline.append({"id": sid, "start": start, "end": end, "voice": secs,
                             "actions_s": elapsed, "overran": max(0.0, over)})
            flag = "  OVERRAN by %.1fs" % over if over > 0 else ""
            print(f"  scene {sid:10s} {start:6.1f}s -> {end:6.1f}s{flag}", flush=True)

        page.wait_for_timeout(900)
        video = page.video.path()
        ctx.close()
        browser.close()

    webm = os.path.join(args.out, "walkthrough.webm")
    if os.path.exists(webm):
        os.remove(webm)
    os.replace(video, webm)
    with open(os.path.join(args.out, "timeline.json"), "w") as f:
        json.dump({"lead": LEAD, "tail": TAIL, "voice": VOICE, "scenes": timeline}, f, indent=2)
    print(f"\nvideo  {webm}\nscenes {len(timeline)}  ·  run assemble.py next")


if __name__ == "__main__":
    main()
