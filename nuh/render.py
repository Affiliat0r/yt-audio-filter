"""Render the Surah Nuh film: python -m nuh.render [--stills ...] [--shot N]

One quasi-static scene (nuh.night) for the whole recitation; the sound is
the recitation, untouched. nuh/data/cuts.json keys gentle changes to the ayat.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CUTS = json.loads((Path(__file__).parent / "data" / "cuts.json").read_text())
FPS = 24
TOTAL = math.ceil(CUTS["audio_seconds"] * FPS)
OUT = ROOT / "output" / "nuh"


def shot_at(T: float):
    cuts = CUTS["cuts"]
    for k in range(len(cuts) - 1):
        if cuts[k] <= T < cuts[k + 1] or k == len(cuts) - 2:
            return k, T - cuts[k], cuts[k + 1] - cuts[k]
    raise ValueError(T)


def frame(f: int) -> np.ndarray:
    from .night import frame as night_frame

    return night_frame(f / FPS, f)


def encode_chunk(args):
    a, b, path = args
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1920x1080", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "grain",
           "-pix_fmt", "yuv420p", "-g", "48", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for f in range(a, b):
        p.stdin.write(frame(f).tobytes())
    p.stdin.close()
    p.wait()
    return a, b, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", help="comma-separated seconds to render as PNGs")
    ap.add_argument("--mid", action="store_true", help="one still from the middle of every shot")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--chunk", type=int, default=96)
    ap.add_argument("--range", help="frames a:b (debug)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.stills or args.mid:
        secs = []
        if args.stills:
            secs += [float(x) for x in args.stills.split(",")]
        if args.mid:
            secs += [(s["start"] + s["end"]) / 2 for s in CUTS["shots"]]
        frames = [int(s * FPS) for s in secs]
        with mp.Pool(args.workers) as pool:
            imgs = pool.map(frame, frames)
        for f, im in zip(frames, imgs):
            cv2.imwrite(str(OUT / f"still_{f:05d}.png"), cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
        print("wrote", len(imgs), "stills to", OUT)
        return

    a0, b0 = (0, TOTAL) if not args.range else map(int, args.range.split(":"))
    chunks_dir = OUT / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    jobs = []
    for a in range(a0, b0, args.chunk):
        b = min(b0, a + args.chunk)
        path = chunks_dir / f"c_{a:05d}.mp4"
        if path.exists() and path.stat().st_size > 0 and not args.range:
            continue
        jobs.append((a, b, path))
    t0 = time.time()
    done = 0
    with mp.Pool(args.workers) as pool:
        for a, b, dt in pool.imap_unordered(encode_chunk, jobs):
            done += b - a
            el = time.time() - t0
            print(f"frames {a}-{b} in {dt:.0f}s | {done}/{sum(j[1]-j[0] for j in jobs)} | {el/60:.1f} min", flush=True)
    if args.range:
        return
    lst = chunks_dir / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in sorted(chunks_dir.glob("c_*.mp4"))))
    silent = OUT / "nuh_picture.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)], check=True)
    final = OUT / "surah_nuh_ahmed_taleb.mp4"
    audio = ROOT / CUTS["audio"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(final)], check=True)
    print("wrote", final)


if __name__ == "__main__":
    main()
