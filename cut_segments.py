"""Helper script to remove copyright-claimed segments from a processed video and re-upload."""

import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from yt_audio_filter.ffmpeg import remove_segments, setup_ffmpeg_path
from yt_audio_filter.logger import setup_logger
from yt_audio_filter.uploader import upload_to_youtube

def parse_timestamp(timestamp: str) -> float:
    """Convert timestamp string (MM:SS or HH:MM:SS) to seconds."""
    parts = timestamp.split(':')
    if len(parts) == 2:
        # MM:SS
        minutes, seconds = parts
        return int(minutes) * 60 + int(seconds)
    elif len(parts) == 3:
        # HH:MM:SS
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    else:
        raise ValueError(f"Invalid timestamp format: {timestamp}")


def main():
    """Remove copyright segments from video and re-upload."""
    logger = setup_logger(verbose=True)

    # Setup FFmpeg
    setup_ffmpeg_path()

    # Video to process
    video_id = "1_ToQOM-jis"
    input_file = Path("output") / f"{video_id}_filtered.mp4"
    output_file = Path("output") / f"{video_id}_filtered_cut.mp4"

    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        logger.info("Checking for alternative locations...")

        # Try cache directory
        cache_file = Path("cache") / "youtube" / f"{video_id}_filtered.mp4"
        if cache_file.exists():
            input_file = cache_file
            logger.info(f"Found file in cache: {input_file}")
        else:
            logger.error("Please ensure the processed video exists in the output directory")
            return 1

    # Copyright claimed segments (MM:SS format)
    remove_timestamps = [
        ("0:06", "0:30"),      # 24 seconds
        ("4:24", "10:33"),     # 6 min 9 sec
        ("14:21", "15:40"),    # 1 min 19 sec
        ("19:28", "20:48"),    # 1 min 20 sec
        ("24:27", "25:45"),    # 1 min 18 sec
        ("29:41", "31:03"),    # 1 min 22 sec
        ("34:59", "36:15"),    # 1 min 16 sec
        ("40:37", "41:53"),    # 1 min 16 sec
        ("45:32", "46:51"),    # 1 min 19 sec
        ("51:16", "51:52"),    # 36 seconds
    ]

    # Convert to seconds
    remove_ranges = [(parse_timestamp(start), parse_timestamp(end))
                     for start, end in remove_timestamps]

    logger.info(f"Processing: {input_file}")
    logger.info(f"Removing {len(remove_ranges)} copyright-claimed segments")

    # Show segments to be removed
    total_removed = 0
    for i, ((start, end), (start_ts, end_ts)) in enumerate(zip(remove_ranges, remove_timestamps), 1):
        duration = end - start
        total_removed += duration
        logger.info(f"  Segment {i}: {start_ts} - {end_ts} ({duration:.1f}s)")

    logger.info(f"Total duration to remove: {total_removed:.1f}s ({total_removed/60:.1f} minutes)")

    # Remove segments
    logger.info("Cutting segments...")
    remove_segments(input_file, output_file, remove_ranges)

    logger.info(f"Successfully created: {output_file}")

    # Ask user if they want to upload
    response = input("\nDo you want to upload this video to YouTube? (yes/no): ").strip().lower()

    if response in ['yes', 'y']:
        logger.info("Uploading to YouTube...")

        # Use a generic title since we don't have the original metadata
        video_id = upload_to_youtube(
            video_path=output_file,
            original_metadata=None,
            privacy="public",  # Change to "unlisted" or "private" if needed
            playlist_id=None,
        )

        logger.info(f"Upload complete! https://youtube.com/watch?v={video_id}")
    else:
        logger.info("Skipping upload. You can upload later with:")
        logger.info(f"  yt-audio-filter {output_file} --upload --privacy public")

    return 0


if __name__ == "__main__":
    sys.exit(main())
