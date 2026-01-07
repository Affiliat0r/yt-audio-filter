"""Daily video processing scheduler.

Fetches videos from configured channels, filters by duration,
excludes already processed videos, and processes up to N videos per day.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from .exceptions import YTAudioFilterError
from .logger import get_logger, setup_logger
from .scraper import VideoInfo, get_channel_videos

logger = get_logger()

# Default channels to process
DEFAULT_CHANNELS = [
    "@niloyatv",  # Niloya
    "https://www.youtube.com/channel/UCuhpMCRmMn5ykbqWPcfzfRQ",  # Baykuş Hop Hop
    "@sevimli.dostlar",  # Sevimli Dostlar
    "https://www.youtube.com/channel/UCrOSyOxCMTuRcRUvWNCt9OA",  # Pırıl
]

# Duration limits (in seconds)
MIN_DURATION = 10 * 60   # 10 minutes
MAX_DURATION = 60 * 60   # 60 minutes

# Processed videos tracking file
PROCESSED_FILE = "processed_videos.json"


class SchedulerError(YTAudioFilterError):
    """Scheduler-related errors."""
    pass


@dataclass
class ProcessedVideo:
    """Record of a processed video."""
    video_id: str
    title: str
    channel: str
    processed_at: str
    uploaded_id: Optional[str] = None


def load_processed_videos(file_path: Path) -> Set[str]:
    """Load set of processed video IDs from tracking file."""
    if not file_path.exists():
        return set()

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("processed_ids", []))
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Could not load processed videos: {e}")
        return set()


def save_processed_video(file_path: Path, video: VideoInfo, channel: str, uploaded_id: Optional[str] = None):
    """Add a video to the processed tracking file."""
    # Load existing data
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {"processed_ids": [], "history": []}
    else:
        data = {"processed_ids": [], "history": []}

    # Add to processed IDs
    if video.video_id not in data["processed_ids"]:
        data["processed_ids"].append(video.video_id)

    # Add to history
    data["history"].append({
        "video_id": video.video_id,
        "title": video.title,
        "channel": channel,
        "duration": video.duration,
        "processed_at": datetime.utcnow().isoformat(),
        "uploaded_id": uploaded_id,
    })

    # Save
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_eligible_videos(
    channel: str,
    processed_ids: Set[str],
    min_duration: int = MIN_DURATION,
    max_duration: int = MAX_DURATION,
    max_videos: int = 200,
) -> List[VideoInfo]:
    """
    Get eligible videos from a channel that haven't been processed.

    Args:
        channel: Channel URL or handle
        processed_ids: Set of already processed video IDs
        min_duration: Minimum video duration in seconds
        max_duration: Maximum video duration in seconds
        max_videos: Max videos to scan from channel

    Returns:
        List of eligible VideoInfo objects, sorted by upload date (newest first)
    """
    eligible = []

    try:
        for video in get_channel_videos(channel, max_videos=max_videos):
            # Skip already processed
            if video.video_id in processed_ids:
                logger.debug(f"Skipping already processed: {video.title}")
                continue

            # Filter by duration
            if video.duration < min_duration:
                logger.debug(f"Skipping too short ({video.duration}s): {video.title}")
                continue

            if video.duration > max_duration:
                logger.debug(f"Skipping too long ({video.duration}s): {video.title}")
                continue

            eligible.append(video)
            logger.debug(f"Eligible: {video.title} ({video.duration}s)")

    except Exception as e:
        logger.error(f"Error scraping {channel}: {e}")
        return []

    # Sort by upload date (newest first)
    eligible.sort(key=lambda v: v.upload_date, reverse=True)

    return eligible


def select_videos_for_processing(
    channels: List[str],
    processed_file: Path,
    videos_per_channel: int = 1,
    min_duration: int = MIN_DURATION,
    max_duration: int = MAX_DURATION,
) -> List[tuple[str, VideoInfo]]:
    """
    Select videos to process from multiple channels.

    Args:
        channels: List of channel URLs/handles
        processed_file: Path to processed videos tracking file
        videos_per_channel: Number of videos to select per channel
        min_duration: Minimum duration in seconds
        max_duration: Maximum duration in seconds

    Returns:
        List of (channel, VideoInfo) tuples
    """
    processed_ids = load_processed_videos(processed_file)
    logger.info(f"Loaded {len(processed_ids)} previously processed video IDs")

    selected = []

    for channel in channels:
        logger.info(f"Scanning channel: {channel}")

        eligible = get_eligible_videos(
            channel,
            processed_ids,
            min_duration=min_duration,
            max_duration=max_duration,
        )

        if not eligible:
            logger.warning(f"No eligible videos found for {channel}")
            continue

        # Select the requested number of videos
        for video in eligible[:videos_per_channel]:
            selected.append((channel, video))
            logger.info(f"Selected: {video.title} ({video.duration // 60}min) from {channel}")

    return selected


def run_daily_pipeline(
    channels: Optional[List[str]] = None,
    processed_file: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    videos_per_channel: int = 1,
    dry_run: bool = False,
    device: str = "auto",
    model_name: str = "htdemucs",
    privacy: str = "unlisted",
) -> int:
    """
    Run the daily video processing pipeline.

    Args:
        channels: List of channels to process (default: DEFAULT_CHANNELS)
        processed_file: Path to tracking file (default: ./processed_videos.json)
        output_dir: Directory for output files (default: ./output)
        videos_per_channel: Videos to process per channel
        dry_run: If True, only print what would be processed
        device: Processing device (auto, cpu, cuda)
        model_name: Demucs model name
        privacy: YouTube upload privacy setting

    Returns:
        Number of successfully processed videos
    """
    if channels is None:
        channels = DEFAULT_CHANNELS

    if processed_file is None:
        processed_file = Path(PROCESSED_FILE)

    if output_dir is None:
        output_dir = Path("output")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Select videos to process
    selected = select_videos_for_processing(
        channels=channels,
        processed_file=processed_file,
        videos_per_channel=videos_per_channel,
    )

    if not selected:
        logger.warning("No videos selected for processing")
        return 0

    logger.info(f"Selected {len(selected)} videos for processing")

    if dry_run:
        print("\n=== DRY RUN - Would process these videos ===\n")
        for i, (channel, video) in enumerate(selected, 1):
            duration_min = video.duration // 60
            print(f"{i}. [{channel}] {video.title}")
            print(f"   Duration: {duration_min} min | URL: {video.url}")
            print()
        return len(selected)

    # Import processing modules
    from .pipeline import process_video
    from .uploader import upload_to_youtube
    from .utils import create_temp_dir
    from .youtube import download_youtube_video

    success_count = 0

    for i, (channel, video) in enumerate(selected, 1):
        logger.info(f"\n=== Processing {i}/{len(selected)}: {video.title} ===")

        try:
            with create_temp_dir(prefix="yt_scheduler_") as temp_dir:
                # Download
                logger.info("Downloading...")
                metadata = download_youtube_video(video.url, temp_dir)

                # Process
                output_path = temp_dir / f"{video.video_id}_filtered.mp4"
                logger.info("Processing with Demucs...")
                process_video(
                    metadata.file_path,
                    output_path,
                    device=device,
                    model_name=model_name,
                )

                # Upload
                logger.info("Uploading to YouTube...")
                uploaded_id = upload_to_youtube(
                    video_path=output_path,
                    original_metadata=metadata,
                    privacy=privacy,
                )

                # Track as processed
                save_processed_video(processed_file, video, channel, uploaded_id)

                logger.info(f"Success! Uploaded as: https://youtube.com/watch?v={uploaded_id}")
                success_count += 1

        except Exception as e:
            logger.error(f"Failed to process {video.title}: {e}")
            # Still mark as processed to avoid retrying failed videos
            save_processed_video(processed_file, video, channel, None)
            continue

    logger.info(f"\n=== Completed: {success_count}/{len(selected)} videos processed ===")
    return success_count


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for scheduler CLI."""
    parser = argparse.ArgumentParser(
        prog="yt-scheduler",
        description="Daily video processing scheduler for YT Audio Filter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  yt-scheduler                          Process 1 video per channel (4 total)
  yt-scheduler --dry-run                Show what would be processed
  yt-scheduler -n 2                     Process 2 videos per channel
  yt-scheduler --channels @Niloya       Only process Niloya channel
  yt-scheduler --device cuda            Use GPU for processing
        """
    )

    parser.add_argument(
        "-n", "--videos-per-channel",
        type=int,
        default=1,
        help="Number of videos to process per channel (default: 1)"
    )

    parser.add_argument(
        "--channels",
        nargs="+",
        default=None,
        help=f"Channels to process (default: {', '.join(DEFAULT_CHANNELS)})"
    )

    parser.add_argument(
        "--processed-file",
        type=Path,
        default=None,
        help="Path to processed videos tracking file"
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output files"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be processed, don't actually process"
    )

    parser.add_argument(
        "--device",
        type=str,
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="Processing device (default: auto)"
    )

    parser.add_argument(
        "--model",
        type=str,
        default="htdemucs",
        help="Demucs model name (default: htdemucs)"
    )

    parser.add_argument(
        "--privacy",
        type=str,
        choices=["public", "unlisted", "private"],
        default="unlisted",
        help="YouTube upload privacy (default: unlisted)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output except errors"
    )

    return parser


def main(args=None) -> int:
    """Main entry point for scheduler CLI."""
    try:
        parser = create_parser()
        parsed = parser.parse_args(args)

        # Setup logging
        setup_logger(verbose=parsed.verbose, quiet=parsed.quiet)

        logger.info("Starting daily processing pipeline...")
        logger.info(f"Channels: {parsed.channels or DEFAULT_CHANNELS}")
        logger.info(f"Videos per channel: {parsed.videos_per_channel}")

        count = run_daily_pipeline(
            channels=parsed.channels,
            processed_file=parsed.processed_file,
            output_dir=parsed.output_dir,
            videos_per_channel=parsed.videos_per_channel,
            dry_run=parsed.dry_run,
            device=parsed.device,
            model_name=parsed.model,
            privacy=parsed.privacy,
        )

        if parsed.dry_run:
            return 0

        return 0 if count > 0 else 1

    except SchedulerError as e:
        print(f"Error: {e}", file=sys.stderr)
        if e.details:
            print(f"Details: {e.details}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        print("\nCancelled by user", file=sys.stderr)
        return 130

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
