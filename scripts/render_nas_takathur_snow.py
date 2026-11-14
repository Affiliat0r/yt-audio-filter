"""One-off render: An-Nas to At-Takathur (13 surahs, Othman Al-Haddad) over a
trimmed clip of Toy Factory's "Snow City".

Only the first ~3 minutes of that video are snow; after 195 s it turns green and
a Halloween segment turns up near 28 minutes. So the visual is the clean snowy
stretch alone (6.5 s to 195.5 s: the "SNOW CITY" title has faded, and the fade to
the Toy Factory logo card has not begun), looped under the recitation by
render_overlay's -stream_loop. The "Winter Train Cartoon" video was rejected: its
hills carry Santa-sleigh and reindeer silhouettes.

The audio is the same locally cut excerpt render_nas_takathur.py used.
"""

import subprocess
from pathlib import Path

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.ffmpeg_overlay import render_overlay
from yt_audio_filter.metadata import load_metadata
from yt_audio_filter.upscale import get_or_create_upscaled
from yt_audio_filter.youtube import download_stream

VISUAL_URL = "https://www.youtube.com/watch?v=eV3FzRwax4k"
SNOW_ID = "eV3FzRwax4k_snow"  # cache key for the trimmed clip, not a YouTube id
SNOW_START = 6.5
SNOW_LENGTH = 189.0
AUDIO_PATH = Path("cache/audio_nas_takathur_haddad.m4a")
CACHE_DIR = Path("cache")
OUTPUT_PATH = Path("output/nas_takathur_haddad_eV3FzRwax4k_snow.mp4")
METADATA_PATH = Path("examples/metadata-surah-nas-takathur-haddad-snow.json")


def trim_snow(source: Path) -> Path:
    """The snowy clip, re-encoded (an accurate cut, not a keyframe-snapped one)."""
    clip = CACHE_DIR / f"video_{SNOW_ID}.mp4"
    if clip.exists():
        return clip
    partial = clip.with_name(f"{clip.stem}.partial{clip.suffix}")
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-v", "error", "-y",
                "-ss", str(SNOW_START), "-t", str(SNOW_LENGTH), "-i", str(source),
                "-an", "-c:v", "libx264", "-crf", "12", "-preset", "fast",
                "-pix_fmt", "yuv420p", str(partial),
            ],
            check=True,
        )
        partial.replace(clip)
    finally:
        partial.unlink(missing_ok=True)
    return clip


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    metadata = load_metadata(METADATA_PATH)

    print("[1/4] Downloading visual (video-only)...")
    source = download_stream(url=VISUAL_URL, output_dir=CACHE_DIR, mode="video-only")

    print("[2/4] Trimming the snowy stretch...")
    clip = trim_snow(Path(source))

    print("[3/4] Upscaling the clip via Real-ESRGAN (cached)...")
    video_path = get_or_create_upscaled(clip, SNOW_ID, CACHE_DIR)

    print("[4/4] Rendering overlay...")
    if metadata.logo_path is None or not metadata.logo_path.exists():
        raise OverlayError(f"Logo file not found: {metadata.logo_path}")

    render_overlay(
        video_path=video_path,
        audio_path=AUDIO_PATH,
        output_path=OUTPUT_PATH,
        resolution=(1280, 720),
        logo=(metadata.logo_path, metadata.logo_position),
        max_duration=None,
        force=True,
    )
    print("Rendered:", OUTPUT_PATH)


if __name__ == "__main__":
    main()
