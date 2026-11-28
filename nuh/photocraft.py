"""Develop the raw loop in PhotoCraft and build the film.

    python -m nuh.photocraft                 # develop every raw frame, then the film
    python -m nuh.photocraft --develop 131   # one frame, for a look

Blender (nuh.blend_shot --raw) writes each frame as linear 32-bit EXR, the
sensor's raw data with no grade. PhotoCraft (github.com/storytold/photocraft,
a Photoshop-style editor driven headless through its CLI) develops it, the
way a photographer develops a raw file:

  Exposure (32-bit, linear)  ->  16 Bits/Channel (to display)  ->  a glow:
  the image duplicated, Gaussian-blurred and Screened over itself  ->
  Curves  ->  Hue/Saturation  ->  Color Balance (cool shadows)  ->
  Lens Correction (lateral chromatic aberration, vignette)  ->  Add Noise
  (film grain, a new seed every frame)

PhotoCraft has no video, so ffmpeg only encodes: the 192 developed frames
become one seamless loop, cycled under the recitation with fades.

The CLI is found at $PHOTOCRAFT_CLI, else in a checkout beside this repo
(../storytold/photocraft/target/release/photocraft-cli); build it with
`cargo build --release -p photocraft-cli`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "nuh"
RAW = OUT / "raw"
FRAMES = OUT / "frames"
CUTS = json.loads((Path(__file__).parent / "data" / "cuts.json").read_text())
LOOP_FRAMES = 192
FPS = 24


def cli() -> str:
    env = os.environ.get("PHOTOCRAFT_CLI")
    if env:
        return env
    for p in (ROOT.parent / "storytold" / "photocraft", ROOT.parent / "photocraft"):
        exe = p / "target" / "release" / "photocraft-cli"
        if exe.exists():
            return str(exe)
    sys.exit("photocraft-cli not found: set PHOTOCRAFT_CLI or build it (cargo build --release -p photocraft-cli)")


def recipe(fi: int, width: int = 1920):
    """The develop, as PhotoCraft engine commands. Pixel sizes scale with the frame."""
    k = width / 1920
    return [
        ("image.adjustments.exposure", {"exposure": 0.7}),
        ("image.mode.bits16", {}),
        ("layer.duplicate", {"name": "glow"}),
        ("filter.blur.gaussianBlur", {"radius": 45 * k}),
        ("layer.setProps", {"blend": "Screen", "opacity": 0.22}),
        ("layer.flattenImage", {}),
        ("image.adjustments.curves", {"points": [[0, 5], [48, 38], [128, 126], [200, 212], [255, 246]]}),
        ("image.adjustments.hueSaturation", {"saturation": -14}),
        ("image.adjustments.colorBalance", {"shadows": [-5, 0, 7], "midtones": [-2, 0, 3], "highlights": [0, 0, -2]}),
        ("filter.lensCorrection", {"profile": "none", "redCyan": 14, "blueYellow": -9, "vignetteAmount": -42, "vignetteMidpoint": 38}),
        ("filter.noise.addNoise", {"amount": 2.2 * max(k, 0.6), "distribution": "gaussian", "monochromatic": True, "seed": 1000 + fi}),
    ]


def develop(fi: int, force: bool = False, raw: Path = RAW, frames: Path = FRAMES, width: int = 1920) -> Path:
    src = raw / f"f{fi:04d}.exr"
    dst = frames / f"f{fi:04d}.png"
    if dst.exists() and not force:
        return dst
    if not src.exists():
        sys.exit(f"missing raw frame {src}: render it with python -m nuh.blend_shot --raw --frames 0:{LOOP_FRAMES}")
    frames.mkdir(parents=True, exist_ok=True)
    cmd = [cli(), "run", str(src)]
    for cid, params in recipe(fi, width):
        cmd += ["--cmd", cid, "--params", json.dumps(params)]
    tmp = dst.with_suffix(".part.png")
    cmd += ["--out", str(tmp), "--metadata", "none"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    tmp.replace(dst)
    return dst


def run(cmd):
    print(" ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True)


def film():
    loop = OUT / "leaf_loop.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", str(FRAMES / "f%04d.png"), "-frames:v", str(LOOP_FRAMES),
         "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p", "-g", str(FPS), str(loop)])
    audio = ROOT / CUTS["audio"]
    total = CUTS["audio_seconds"]
    last = CUTS["shots"][-1]["start"] + CUTS["events"]["28"]["end"]
    fo = last + 0.3
    vf = f"fade=t=in:st=0:d=5,fade=t=out:st={fo:.2f}:d={total - fo:.2f}"
    out = OUT / "surah_nuh_leaf.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(loop), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-t", f"{total:.2f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-tune", "film", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(out)])
    small = OUT / "surah_nuh_leaf_720p.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vf", "scale=1280:720", "-c:v", "libx264",
         "-preset", "slow", "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(small)])
    print("wrote", out, "and", small)


PREVIEW = OUT / "preview"


def preview():
    """The half-size, low-sample loop (python -m nuh.blend_shot --raw --scale 50 --samples 16
    --frames 0:192 --out-dir output/nuh/preview/raw), developed the same way, played twice
    over the opening of the recitation."""
    frames = PREVIEW / "frames"
    for fi in range(LOOP_FRAMES):
        develop(fi, raw=PREVIEW / "raw", frames=frames, width=960)
    loop = PREVIEW / "loop.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", str(frames / "f%04d.png"), "-frames:v", str(LOOP_FRAMES),
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(loop)])
    out = PREVIEW / "nuh_preview.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "1", "-i", str(loop), "-i", str(ROOT / CUTS["audio"]),
         "-map", "0:v:0", "-map", "1:a:0", "-t", "16", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    print("wrote", out)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--develop", type=int, help="develop one loop frame and stop")
    ap.add_argument("--force", action="store_true", help="develop again even if the frame exists")
    ap.add_argument("--preview", action="store_true", help="develop the low-resolution preview loop")
    a = ap.parse_args(argv)
    if a.preview:
        preview()
        return
    if a.develop is not None:
        print(develop(a.develop, force=True))
        return
    for fi in range(LOOP_FRAMES):
        develop(fi, a.force)
    film()


if __name__ == "__main__":
    main(sys.argv[1:])
