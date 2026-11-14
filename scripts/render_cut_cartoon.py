"""Render a cartoon compilation WITHOUT uploading, then cut flagged episodes out.

Why this exists: `workflow_cli --approve` renders and publishes in one step, so
it cannot remove an episode first. Gecko's Garage compilations are built from the
same recycled specials (Halloween, Noel, Dia de los Muertos, a birthday), so the
episode has to be cut from the *render*, before anything is public.

Episode numbers are the ones `scripts/screen_video.py` prints (E1 is the 6 s
intro clip, E2 the first real episode), counted by black-frame separators.

    python scripts/render_cut_cartoon.py B_TLeuhchhk --cut 8

Writes `output/<stem>_cut.mp4`. Never uploads.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

CACHE_DIR = Path("cache")
OUTPUT_DIR = Path("output")


def find_boundaries(path: Path) -> List[Tuple[float, float]]:
    """(black_start, black_end) for every episode separator in ``path``."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-vf", "blackdetect=d=0.04:pix_th=0.12", "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return [
        (float(a), float(b))
        for a, b in re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", proc.stderr)
    ]


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def keep_intervals(
    boundaries: List[Tuple[float, float]], cuts: List[int], total: float
) -> List[Tuple[float, float]]:
    """Intervals to keep once episodes ``cuts`` (1-based) are removed.

    Episode k lies between separator k-1 and separator k. Dropping it removes
    from the start of the black before it to the end of the black after it, so
    its neighbours join with no black frame left over.
    """
    episodes = len(boundaries) + 1
    for k in cuts:
        if not 1 <= k <= episodes:
            raise SystemExit(f"--cut {k}: this render has {episodes} episodes (E1..E{episodes})")
    removed: List[Tuple[float, float]] = []
    for k in sorted(set(cuts)):
        start = boundaries[k - 2][0] if k >= 2 else 0.0
        end = boundaries[k - 1][1] if k <= len(boundaries) else total
        if removed and start <= removed[-1][1]:
            removed[-1] = (removed[-1][0], max(removed[-1][1], end))
        else:
            removed.append((start, end))
    keep: List[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in removed:
        if start > cursor:
            keep.append((cursor, start))
        cursor = end
    if cursor < total:
        keep.append((cursor, total))
    return keep


def cut_episodes(src: Path, dst: Path, keep: List[Tuple[float, float]]) -> None:
    """Re-encode the kept intervals in ONE pass (no stitched -c copy joins)."""
    parts: List[str] = []
    for i, (a, b) in enumerate(keep):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    labels = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
    parts.append(f"{labels}concat=n={len(keep)}:v=1:a=1[v][a]")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src),
            "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
            "-cq", "19", "-b:v", "0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart",
            str(dst),
        ],
        check=True,
    )


def render(video_id: str) -> Path:
    """Download, sharpen, strip the music. The same steps the workflow runs."""
    from yt_audio_filter import pipeline, upscale, youtube
    from yt_audio_filter.workflow_runner import _probe_height, scale_height_for

    url = f"https://www.youtube.com/watch?v={video_id}"
    meta = youtube.download_video_with_metadata(url, CACHE_DIR, use_cache=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{Path(meta.file_path).stem}_filtered.mp4"
    source = Path(meta.file_path)
    try:
        source = Path(upscale.get_or_create_sharpened(source, video_id=video_id, cache_dir=CACHE_DIR))
    except Exception as exc:  # noqa: BLE001 - same best-effort contract as the workflow
        print(f"upscale-skipped: {exc}", file=sys.stderr)
    scale = scale_height_for(_probe_height(source), 720)
    return Path(pipeline.process_video(input_path=source, output_path=output, scale_height=scale))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("video_id")
    parser.add_argument("--cut", type=int, action="append", default=[],
                        help="episode number to remove (from screen_video.py); repeatable")
    args = parser.parse_args()

    rendered = render(args.video_id)
    print(f"RENDERED {rendered} ({duration_of(rendered):.2f}s)", flush=True)
    boundaries = find_boundaries(rendered)
    total = duration_of(rendered)
    print(f"{len(boundaries) + 1} episodes; separators at {[round(b[0], 1) for b in boundaries]}")
    keep = keep_intervals(boundaries, args.cut, total)
    print("keeping", [(round(a, 1), round(b, 1)) for a, b in keep])
    cut = rendered.with_name(rendered.stem.replace("_filtered", "") + "_cut.mp4")
    cut_episodes(rendered, cut, keep)
    print(f"CUT {cut} ({duration_of(cut):.2f}s)")


if __name__ == "__main__":
    main()
