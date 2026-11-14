"""Film an Elif-Ba episode: storyboard -> HTML engine -> frames + mixed audio -> MP4.

    python -m elifba.render 1                  # episode 1 -> output/elifba/elifba_01.mp4
    python -m elifba.render 1 --stills 3,20,95 # just a few frames, for checking a look

The page draws any frame on demand (``seek(t)``), so frames are captured one by
one rather than screen-recorded: no dropped frames, and identical output every
run. Uses the system Edge through Playwright, so nothing has to be downloaded.

Audio is mixed here, not in the browser: each clip is decoded, levelled to a
common loudness, placed at its scripted time, and the sum is loudness-normalised
once more when muxed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List

import numpy as np

from elifba.storyboard import ROOT, SERIES, episode

ENGINE = ROOT / "engine" / "index.html"
OUT_DIR = ROOT.parent / "output" / "elifba"
RATE = 48000
FPS = 30
W, H = 1920, 1080

#: Per-kind level (RMS, dBFS) each clip is brought to before mixing.
LEVELS = {"voice": -18.0, "quran": -19.0}


def decode(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def level(x: np.ndarray, target_db: float) -> np.ndarray:
    """Scale so the RMS of the voiced part sits at ``target_db``."""
    if not len(x):
        return x
    frame = 1024
    n = len(x) // frame
    if n == 0:
        rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    else:
        blocks = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1))
        voiced = blocks[blocks > blocks.max() * 0.1]
        rms = float(np.sqrt(np.mean(voiced ** 2)) + 1e-9)
    return x * (10 ** (target_db / 20) / rms)


def mix(timeline: dict, wav: Path) -> None:
    total = int((timeline["duration"] + 0.5) * RATE)
    bus = np.zeros(total, dtype=np.float32)
    for item in timeline["audio"]:
        clip = level(decode(ROOT / item["path"]), LEVELS[item["kind"]])
        # 8 ms fades so no clip starts or ends with a click
        f = min(len(clip) // 2, int(0.008 * RATE))
        if f:
            ramp = np.linspace(0, 1, f, dtype=np.float32)
            clip[:f] *= ramp
            clip[-f:] *= ramp[::-1]
        start = int(item["t"] * RATE)
        end = min(total, start + len(clip))
        bus[start:end] += clip[: end - start]
    peak = float(np.abs(bus).max() or 1)
    if peak > 0.97:
        bus *= 0.97 / peak
    pcm = (bus * 32767).astype("<i2").tobytes()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-", str(wav)],
        input=pcm, check=True,
    )


def open_page(timeline: dict):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(channel="msedge", headless=True, args=["--allow-file-access-from-files"])
    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    page.add_init_script(f"window.__TIMELINE = {json.dumps(timeline, ensure_ascii=False)};")
    page.goto(ENGINE.as_uri())
    page.evaluate("window.__ready")
    page.wait_for_function("document.fonts.status === 'loaded'")
    return pw, browser, page


def stills(timeline: dict, times: List[float], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pw, browser, page = open_page(timeline)
    paths = []
    try:
        for t in times:
            page.evaluate(f"seek({t})")
            p = out_dir / f"still_{t:07.2f}.png"
            page.screenshot(path=str(p))
            paths.append(p)
    finally:
        browser.close()
        pw.stop()
    return paths


def film(timeline: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    mix(timeline, wav)

    n_frames = int(np.ceil(timeline["duration"] * FPS))
    partial = out.with_name(out.stem + ".partial.mp4")
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
         "-i", str(wav),
         "-map", "0:v", "-map", "1:a",
         "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17", "-b:v", "0",
         "-pix_fmt", "yuv420p", "-profile:v", "high",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", str(RATE),
         "-shortest", "-movflags", "+faststart", str(partial)],
        stdin=subprocess.PIPE,
    )
    pw, browser, page = open_page(timeline)
    t0 = time.time()
    try:
        for i in range(n_frames):
            page.evaluate(f"seek({i / FPS})")
            enc.stdin.write(page.screenshot(type="jpeg", quality=96))
            if i % (FPS * 20) == 0:
                el = time.time() - t0
                print(f"  frame {i}/{n_frames}  {el:.0f}s elapsed", flush=True)
    finally:
        browser.close()
        pw.stop()
        enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    partial.replace(out)
    wav.unlink(missing_ok=True)


def chapters_text(timeline: dict) -> str:
    lines = []
    for c in timeline["chapters"]:
        t = int(c["t"])
        lines.append(f"{t // 60}:{t % 60:02d} {c['title']}")
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("episode", type=int)
    ap.add_argument("--stills", help="comma-separated times; write PNGs instead of a video")
    args = ap.parse_args()

    tl = episode(args.episode, SERIES[args.episode - 1])
    name = f"elifba_{args.episode:02d}"
    (OUT_DIR / f"{name}.timeline.json").parent.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.stills:
        times = [float(x) for x in args.stills.split(",")]
        for p in stills(tl, times, OUT_DIR / f"{name}_stills"):
            print(p)
        return
    out = OUT_DIR / f"{name}.mp4"
    film(tl, out)
    print("Rendered:", out)
    print(chapters_text(tl))


if __name__ == "__main__":
    main()
