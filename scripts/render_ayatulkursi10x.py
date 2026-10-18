"""One-off render: Ayat al-Kursi x10 (Saad Al-Ghamdi, isolated from a full
Al-Baqarah recitation) over a Toy Factory dinosaur visual.

Bespoke because the audio isn't a single YouTube URL run_overlay() could
download itself -- it's a locally trimmed+concatenated excerpt -- so this
replicates run_overlay()'s steps by hand against overlay_pipeline's
building blocks.
"""

from pathlib import Path

from yt_audio_filter.exceptions import OverlayError
from yt_audio_filter.ffmpeg_overlay import render_overlay
from yt_audio_filter.metadata import load_metadata
from yt_audio_filter.upscale import get_or_create_upscaled
from yt_audio_filter.youtube import download_stream

VISUAL_URL = "https://www.youtube.com/watch?v=HMjbCz_4UOA"
VISUAL_ID = "HMjbCz_4UOA"
AUDIO_PATH = Path("cache/audio_ayatulkursi10x_ghamdi.m4a")
CACHE_DIR = Path("cache")
OUTPUT_PATH = Path("output/ayatulkursi10x_ghamdi_HMjbCz_4UOA.mp4")
METADATA_PATH = Path("examples/metadata-ayatulkursi-10x-ghamdi.json")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    metadata = load_metadata(METADATA_PATH)

    print("[1/3] Downloading visual (video-only)...")
    video_path = download_stream(url=VISUAL_URL, output_dir=CACHE_DIR, mode="video-only")

    print("[2/3] Upscaling visual via Real-ESRGAN (cached)...")
    video_path = get_or_create_upscaled(video_path, VISUAL_ID, CACHE_DIR)

    print("[3/3] Rendering overlay...")
    if metadata.logo_path is None or not metadata.logo_path.exists():
        raise OverlayError(f"Logo file not found: {metadata.logo_path}")
    logo_arg = (metadata.logo_path, metadata.logo_position)

    render_overlay(
        video_path=video_path,
        audio_path=AUDIO_PATH,
        output_path=OUTPUT_PATH,
        resolution=(1280, 720),
        logo=logo_arg,
        max_duration=None,
        force=True,
    )
    print("Rendered:", OUTPUT_PATH)


if __name__ == "__main__":
    main()
