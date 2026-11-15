"""One-off render: At-Tariq, Al-Buruj, Al-Inshiqaq — in that order, as the owner
asked ("Tariq till Inshiqaq") — recited by Othman Al-Haddad, over a long snowy
Toy Factory visual.

Audio: cut from his full Juz Amma recitation (VYql3txo_vE) at the pauses
between surahs, each surah with its own besmele, and every join checked by
transcription — cache/audio_tariq_inshiqaq_haddad.m4a, 7:59:
    At-Tariq     1828.4 - 1925.5 s
    Al-Buruj     1612.6 - 1828.4 s
    Al-Inshiqaq  1446.5 - 1612.6 s

Visual: Toy Factory has no long video that stays snowy without Christmas, so
this joins the snowy openings of two, checked frame by frame (every 3 s):
    "Toy Factory Winter Fun"  0PbvwOBM-_M  23 - 175 s  (after the SNOW TOWN title)
    "Happy Winter"            _qKE8fItfFg   8 - 108 s and 137 - 181 s
The cut in "Happy Winter" leaves out a tree with gift boxes under it; the rest
of that video turns green and has a Halloween segment. 4:56 of snow, looped.
"""

import subprocess
from pathlib import Path

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.ffmpeg_overlay import render_overlay
from yt_audio_filter.metadata import load_metadata
from yt_audio_filter.upscale import get_or_create_upscaled
from yt_audio_filter.youtube import download_stream

CACHE_DIR = Path("cache")
AUDIO_PATH = CACHE_DIR / "audio_tariq_inshiqaq_haddad.m4a"
SNOW_ID = "tariq_snow"                     # cache key for the joined clip, not a YouTube id
SEGMENTS = [("0PbvwOBM-_M", 23.0, 175.0), ("_qKE8fItfFg", 8.0, 108.0), ("_qKE8fItfFg", 137.0, 181.0)]
OUTPUT_PATH = Path("output/tariq_inshiqaq_haddad_snow.mp4")
METADATA_PATH = Path("examples/metadata-surah-tariq-inshiqaq-haddad-snow.json")


def build_snow_clip() -> Path:
    """The snowy stretches joined into one clip (re-encoded: accurate cuts)."""
    clip = CACHE_DIR / f"video_{SNOW_ID}.mp4"
    if clip.exists():
        return clip
    sources = {vid: download_stream(url=f"https://www.youtube.com/watch?v={vid}", output_dir=CACHE_DIR, mode="video-only")
               for vid in {s[0] for s in SEGMENTS}}
    inputs, parts = [], []
    for i, (vid, a, b) in enumerate(SEGMENTS):
        inputs += ["-i", str(sources[vid])]
        parts.append(f"[{i}:v]trim={a}:{b},setpts=PTS-STARTPTS,fps=30,scale=640:360,setsar=1[v{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(SEGMENTS))) + f"concat=n={len(SEGMENTS)}:v=1[out]"
    partial = clip.with_name(f"{clip.stem}.partial{clip.suffix}")
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                        "-an", "-c:v", "libx264", "-crf", "12", "-preset", "fast", "-pix_fmt", "yuv420p", str(partial)],
                       check=True)
        partial.replace(clip)
    finally:
        partial.unlink(missing_ok=True)
    return clip


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    metadata = load_metadata(METADATA_PATH)
    print("[1/3] Joining the snowy stretches...")
    clip = build_snow_clip()
    print("[2/3] Upscaling the clip via Real-ESRGAN (cached)...")
    video_path = get_or_create_upscaled(clip, SNOW_ID, CACHE_DIR)
    print("[3/3] Rendering overlay...")
    if metadata.logo_path is None or not metadata.logo_path.exists():
        raise OverlayError(f"Logo file not found: {metadata.logo_path}")
    render_overlay(video_path=video_path, audio_path=AUDIO_PATH, output_path=OUTPUT_PATH, resolution=(1280, 720),
                   logo=(metadata.logo_path, metadata.logo_position), max_duration=None, force=True)
    print("Rendered:", OUTPUT_PATH)


if __name__ == "__main__":
    main()
