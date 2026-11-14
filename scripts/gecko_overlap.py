"""Which episodes of a Gecko compilation are already on the channel?

Gecko's Garage compilations are built from one shared pool of ~2-minute episodes,
so a "new" compilation is mostly repeats. A title says nothing about that, so this
fingerprints the episodes themselves: split each file on its black-frame
separators, take a 16x16 average-hash of three frames per episode, and compare.

    python scripts/gecko_overlap.py --published output/a.mp4 output/b.mp4 \
        --candidate cache/video_X.mp4 cache/video_Y.mp4

Prints, per candidate, each episode's start, length and whether (and where) it was
already published. Episode numbers match scripts/screen_video.py (E1 is the 6 s
intro clip, E2 the first real episode), so the holiday hits that script reports
line up with these rows.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
FP_DIR = Path("cache/_fp")
OFFSETS = (3.0, 25.0, 50.0)
SAME_EPISODE_BITS = 40  # mean Hamming distance over the sampled frames, of 256
LENGTH_SLACK = 2.5  # seconds; renders drift up to ~0.4 s from their source


def _load_cutter():
    spec = importlib.util.spec_from_file_location("render_cut_cartoon", HERE / "render_cut_cartoon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cutter = _load_cutter()


def ahash(path: Path, t: float) -> Optional[int]:
    """64-bit-per-row 16x16 average hash of the frame at ``t``, or None."""
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
            "-vf", "scale=16:16:flags=area,format=gray", "-f", "rawvideo", "-",
        ],
        capture_output=True,
    )
    data = proc.stdout
    if len(data) != 256:
        return None
    mean = sum(data) / 256
    bits = 0
    for i, value in enumerate(data):
        if value > mean:
            bits |= 1 << i
    return bits


def fingerprint(path: Path) -> List[Dict]:
    """Episodes of ``path`` with their hashes, cached by file name and size."""
    FP_DIR.mkdir(parents=True, exist_ok=True)
    cache = FP_DIR / f"{path.stem}_{path.stat().st_size}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    total = cutter.duration_of(path)
    seps = cutter.find_boundaries(path)
    starts = [0.0] + [b for _, b in seps]
    ends = [a for a, _ in seps] + [total]
    episodes: List[Dict] = []
    for number, (s, e) in enumerate(zip(starts, ends), start=1):
        length = e - s
        hashes = [h for off in OFFSETS if off < length - 1 for h in [ahash(path, s + off)] if h is not None]
        episodes.append({"n": number, "start": round(s, 2), "length": round(length, 2), "hashes": hashes})
    cache.write_text(json.dumps(episodes))
    return episodes


def distance(a: Dict, b: Dict) -> Optional[float]:
    """Mean Hamming distance over the frames both episodes sampled, or None."""
    if abs(a["length"] - b["length"]) > LENGTH_SLACK:
        return None
    pairs = list(zip(a["hashes"], b["hashes"]))
    if not pairs:
        return None
    return sum(bin(x ^ y).count("1") for x, y in pairs) / len(pairs)


def best_match(ep: Dict, published: List[Tuple[str, List[Dict]]]) -> Optional[Tuple[str, int, float]]:
    best: Optional[Tuple[str, int, float]] = None
    for name, episodes in published:
        for other in episodes:
            d = distance(ep, other)
            if d is not None and d <= SAME_EPISODE_BITS and (best is None or d < best[2]):
                best = (name, other["n"], d)
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--published", nargs="+", required=True, type=Path)
    parser.add_argument("--candidate", nargs="+", required=True, type=Path)
    args = parser.parse_args()

    files = list(args.published) + list(args.candidate)
    with ThreadPoolExecutor(max_workers=3) as pool:
        prints = dict(zip(files, pool.map(fingerprint, files)))
    published = [(p.stem, prints[p]) for p in args.published]

    for cand in args.candidate:
        print(f"\n=== {cand.stem}")
        new_s = 0.0
        for ep in prints[cand]:
            if ep["length"] < 20:  # the 6 s intro clip, or the trailing black sliver
                continue
            match = best_match(ep, published)
            tag = f"DUP of {match[0]} E{match[1]} (d={match[2]:.0f})" if match else "new"
            if not match:
                new_s += ep["length"]
            print(f"  E{ep['n']:<2} {int(ep['start'] // 60):>2}:{int(ep['start'] % 60):02d} {ep['length']:>6.1f}s  {tag}")
        print(f"  -> {new_s / 60:.1f} min not yet published")


if __name__ == "__main__":
    main()
