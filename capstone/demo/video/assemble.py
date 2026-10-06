"""Lay the narration onto the recording, write the subtitle sidecar, encode the mp4.

Reads out/timeline.json (measured, not planned), so the voice lands on the frames that
were actually on screen. Produces:

    out/stickermule-preflight-demo.mp4   h264 + aac, faststart
    out/stickermule-preflight-demo.srt   soft subtitles, same stem
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import wave

import imageio_ffmpeg
import numpy as np

from script import SCENES

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 24000
STEM = "stickermule-preflight-demo"


def read_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        assert w.getframerate() == SR, f"{path}: expected {SR} Hz"
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    for p in parts:                       # keep cue lines readable
        while len(p) > 180:
            cut = p.rfind(",", 60, 180)
            cut = cut if cut > 0 else p.rfind(" ", 60, 180)
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    args = ap.parse_args()

    with open(os.path.join(args.out, "timeline.json")) as f:
        tl = json.load(f)
    scenes = {s["id"]: s for s in tl["scenes"]}
    narration = {sid: text for sid, _a, text in SCENES}
    webm = os.path.join(args.out, "walkthrough.webm")

    total = max(s["end"] for s in tl["scenes"]) + 2.0
    track = np.zeros(int(total * SR) + SR, dtype=np.float32)
    cues: list[tuple[float, float, str]] = []

    for sid, _actions, text in SCENES:
        sc = scenes[sid]
        clip = read_wav(os.path.join(args.out, "voice", f"{sid}.wav"))
        # Narration starts when the scene's actions have finished settling, so the words
        # describe a frame that is already on screen.
        start = sc["start"] + min(sc["actions_s"], max(0.0, sc["end"] - sc["start"] - sc["voice"] - tl["tail"]))
        i = int(start * SR)
        track[i:i + len(clip)] += clip

        lines = sentences(text)
        weights = np.array([len(x) for x in lines], dtype=np.float64)
        bounds = np.concatenate([[0.0], np.cumsum(weights / weights.sum())]) * (len(clip) / SR)
        n = 0
        while n < len(lines):
            m = n                       # merge cues that would flash by too fast to read
            while m + 1 < len(lines) and bounds[m + 1] - bounds[n] < 1.4:
                m += 1
            cues.append((start + bounds[n], start + bounds[m + 1] - 0.04, " ".join(lines[n:m + 1])))
            n = m + 1

    peak = float(np.abs(track).max()) or 1.0
    track = np.clip(track / max(peak, 1.0) * 0.92, -1.0, 1.0)
    voice_wav = os.path.join(args.out, "narration.wav")
    with wave.open(voice_wav, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((track * 32767).astype(np.int16).tobytes())

    srt = os.path.join(args.out, f"{STEM}.srt")
    with open(srt, "w", encoding="utf-8") as f:
        for n, (a, b, line) in enumerate(cues, 1):
            f.write(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{line}\n\n")

    mp4 = os.path.join(args.out, f"{STEM}.mp4")
    subprocess.run([
        FFMPEG, "-y", "-i", webm, "-i", voice_wav,
        "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", mp4,
    ], check=True, capture_output=True)

    probe = subprocess.run([FFMPEG, "-i", mp4], capture_output=True, text=True).stderr
    dur = next(l.split("Duration:")[1].split(",")[0].strip() for l in probe.splitlines() if "Duration:" in l)
    print(f"mp4  {mp4}  ({dur})\nsrt  {srt}  ({len(cues)} cues)")
    over = [s for s in tl["scenes"] if s["overran"] > 0.6]
    if over:
        print("scenes whose actions ran past their narration (voice starts late there):")
        for s in over:
            print(f"  {s['id']:10s} +{s['overran']:.1f}s")


if __name__ == "__main__":
    main()
