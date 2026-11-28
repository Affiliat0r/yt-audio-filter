"""Grade the rendered core loop and build the film.

    python -m nuh.assemble            # after nuh.cycles_loop has rendered every loop frame

1. Each linear frame in output/nuh/loop/fNNNN.npy is graded (nuh.leaf.develop_render)
   to output/nuh/loop/png/.
2. The PNGs become one seamless 8-second loop, output/nuh/leaf_loop.mp4.
3. The loop is cycled under the whole recitation, faded in over the first
   five seconds and out after the last word: output/nuh/surah_nuh_leaf.mp4,
   plus a 720p copy small enough to send to a phone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from .cycles_loop import LOOP_FRAMES
from .leaf import develop_render

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "nuh"
LOOP = OUT / "loop"
CUTS = json.loads((Path(__file__).parent / "data" / "cuts.json").read_text())


def grade():
    png = LOOP / "png"
    png.mkdir(parents=True, exist_ok=True)
    for fi in range(LOOP_FRAMES):
        src = LOOP / f"f{fi:04d}.npy"
        dst = png / f"f{fi:04d}.png"
        if not src.exists():
            sys.exit(f"missing loop frame {fi}: render it first")
        if dst.exists():
            continue
        img = np.load(src).astype(np.float32)
        if img.shape[:2] != (1080, 1920):
            img = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_LANCZOS4)
        out = develop_render(img, fi)
        cv2.imwrite(str(dst), cv2.cvtColor(out, cv2.COLOR_RGB2BGR))
    return png


def run(cmd):
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main():
    png = grade()
    loop_mp4 = OUT / "leaf_loop.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-framerate", "24", "-i", str(png / "f%04d.png"),
         "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p", "-g", "24", str(loop_mp4)])
    audio = ROOT / CUTS["audio"]
    total = CUTS["audio_seconds"]
    last = CUTS["shots"][-1]["start"] + CUTS["events"]["28"]["end"]
    fo = last + 0.3
    vf = f"fade=t=in:st=0:d=5,fade=t=out:st={fo:.2f}:d={total - fo:.2f}"
    film = OUT / "surah_nuh_leaf.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(loop_mp4), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-t", f"{total:.2f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "film", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(film)])
    small = OUT / "surah_nuh_leaf_720p.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(film), "-vf", "scale=1280:720", "-c:v", "libx264",
         "-preset", "slow", "-crf", "24", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(small)])
    print("wrote", film, "and", small)


if __name__ == "__main__":
    main()
