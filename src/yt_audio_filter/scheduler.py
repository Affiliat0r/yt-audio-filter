"""Daily video processing scheduler with autonomous discovery.

Discovers videos via YouTube Data API, scrapes whitelisted channels,
filters by duration and copyright risk, excludes already processed videos,
and processes up to N videos per day.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from .config import DiscoveryConfig, get_api_key, load_config, generate_default_config
from .exceptions import YTAudioFilterError
from .logger import get_logger, setup_logger
from .scraper import VideoInfo, get_channel_videos

logger = get_logger()

# Fallback defaults when no config file exists (empty — API discovery is primary)
DEFAULT_CHANNELS: list[str] = []

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


def save_processed_video(
    file_path: Path,
    video: VideoInfo,
    channel: str,
    uploaded_id: Optional[str] = None,
):
    """Add a video to the processed tracking file."""
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {"processed_ids": [], "history": []}
    else:
        data = {"processed_ids": [], "history": []}

    if video.video_id not in data["processed_ids"]:
        data["processed_ids"].append(video.video_id)

    data["history"].append(
        {
            "video_id": video.video_id,
            "title": video.title,
            "channel": channel,
            "duration": video.duration,
            "processed_at": datetime.utcnow().isoformat(),
            "uploaded_id": uploaded_id,
        }
    )

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_eligible_videos(
    channel: str,
    processed_ids: Set[str],
    min_duration: int = 600,
    max_duration: int = 3600,
    max_videos: int = 200,
) -> List[VideoInfo]:
    """Get eligible videos from a channel that haven't been processed."""
    eligible = []

    try:
        for video in get_channel_videos(channel, max_videos=max_videos):
            if video.video_id in processed_ids:
                logger.debug(f"Skipping already processed: {video.title}")
                continue

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

    eligible.sort(key=lambda v: v.upload_date, reverse=True)
    return eligible


def select_videos_for_processing(
    channels: List[str],
    processed_file: Path,
    videos_per_channel: int = 1,
    min_duration: int = 600,
    max_duration: int = 3600,
) -> List[tuple[str, VideoInfo]]:
    """Select videos to process from multiple channels (legacy channel scraping)."""
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

        for video in eligible[:videos_per_channel]:
            selected.append((channel, video))
            logger.info(f"Selected: {video.title} ({video.duration // 60}min) from {channel}")

    return selected


def _discover_via_api(
    config: DiscoveryConfig,
    api_key: str,
    processed_ids: Set[str],
    max_candidates: int,
) -> List[tuple[str, VideoInfo]]:
    """Discover videos via YouTube Data API and return as (channel, VideoInfo) tuples."""
    from .discovery import discover_videos

    candidates = discover_videos(
        config=config,
        api_key=api_key,
        processed_ids=processed_ids,
        max_candidates=max_candidates,
    )

    # Convert VideoCandidate to (channel, VideoInfo) for pipeline compatibility
    result = []
    for candidate in candidates:
        video_info = candidate.to_video_info()
        channel = candidate.channel_title or candidate.channel_id
        result.append((channel, video_info))

    return result


def run_daily_pipeline(
    config: Optional[DiscoveryConfig] = None,
    channels: Optional[List[str]] = None,
    processed_file: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    videos_per_run: Optional[int] = None,
    dry_run: bool = False,
    discover_only: bool = False,
    device: Optional[str] = None,
    model_name: Optional[str] = None,
    privacy: Optional[str] = None,
    api_key: Optional[str] = None,
) -> int:
    """Run the daily video processing pipeline.

    Uses YouTube Data API discovery as primary source when an API key is available,
    with channel scraping as a secondary source for whitelisted channels.

    Args:
        config: Discovery configuration (loaded from YAML if None)
        channels: Override channels list (bypasses config)
        processed_file: Path to tracking file
        output_dir: Directory for output files
        videos_per_run: Total videos to process this run
        dry_run: Only print what would be processed
        discover_only: Only discover, don't process
        device: Processing device (auto, cpu, cuda)
        model_name: Demucs model name
        privacy: YouTube upload privacy setting
        api_key: YouTube Data API key

    Returns:
        Number of successfully processed videos
    """
    # Load config if not provided
    if config is None:
        config = load_config()

    # Apply defaults from config, allow CLI overrides
    if processed_file is None:
        processed_file = Path(PROCESSED_FILE)
    if output_dir is None:
        output_dir = Path(config.scheduler.output_dir)
    if videos_per_run is None:
        videos_per_run = config.scheduler.videos_per_run
    if device is None:
        device = config.scheduler.device
    if model_name is None:
        model_name = config.scheduler.model_name
    if privacy is None:
        privacy = config.scheduler.privacy

    output_dir.mkdir(parents=True, exist_ok=True)
    processed_ids = load_processed_videos(processed_file)
    logger.info(f"Loaded {len(processed_ids)} previously processed video IDs")

    all_selected: List[tuple[str, VideoInfo]] = []
    seen_ids: Set[str] = set()

    # Phase 1: Discovery via YouTube Data API
    if api_key:
        logger.info("=== Phase 1: YouTube Data API Discovery ===")
        try:
            discovered = _discover_via_api(
                config=config,
                api_key=api_key,
                processed_ids=processed_ids,
                max_candidates=videos_per_run,
            )
            for channel, video in discovered:
                if video.video_id not in seen_ids:
                    all_selected.append((channel, video))
                    seen_ids.add(video.video_id)
            logger.info(f"API discovery: {len(discovered)} candidates")
        except Exception as e:
            logger.warning(f"API discovery failed, falling back to channel scraping: {e}")
    else:
        logger.info("No API key provided, skipping YouTube Data API discovery")

    # Phase 2: Channel scraping for whitelisted channels
    scrape_channels = channels or config.channels.whitelisted_channels or DEFAULT_CHANNELS
    remaining_slots = max(0, videos_per_run - len(all_selected))

    if remaining_slots > 0 and scrape_channels:
        logger.info(f"=== Phase 2: Channel Scraping ({remaining_slots} slots remaining) ===")
        videos_per_channel = max(1, remaining_slots // len(scrape_channels))

        for channel in scrape_channels:
            if len(all_selected) >= videos_per_run:
                break

            logger.info(f"Scanning channel: {channel}")
            eligible = get_eligible_videos(
                channel,
                processed_ids | seen_ids,
                min_duration=config.duration.min_seconds,
                max_duration=config.duration.max_seconds,
            )

            for video in eligible[:videos_per_channel]:
                if video.video_id not in seen_ids and len(all_selected) < videos_per_run:
                    all_selected.append((channel, video))
                    seen_ids.add(video.video_id)
                    logger.info(
                        f"Selected: {video.title} ({video.duration // 60}min) from {channel}"
                    )

    if not all_selected:
        logger.warning("No videos selected for processing")
        return 0

    logger.info(f"Total selected: {len(all_selected)} videos")

    # Dry run or discover-only: print and exit
    if dry_run or discover_only:
        print(f"\n=== {'DRY RUN' if dry_run else 'DISCOVERY'} - Selected videos ===\n")
        for i, (channel, video) in enumerate(all_selected, 1):
            duration_min = video.duration // 60
            print(f"{i}. [{channel}] {video.title}")
            print(f"   Duration: {duration_min} min | URL: {video.url}")
            print()
        return len(all_selected)

    # Import processing modules
    from .pipeline import process_video
    from .uploader import upload_to_youtube
    from .utils import create_temp_dir
    from .youtube import download_youtube_video

    success_count = 0

    for i, (channel, video) in enumerate(all_selected, 1):
        logger.info(f"\n=== Processing {i}/{len(all_selected)}: {video.title} ===")

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
            save_processed_video(processed_file, video, channel, None)
            continue

    logger.info(f"\n=== Completed: {success_count}/{len(all_selected)} videos processed ===")
    return success_count


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for scheduler CLI."""
    parser = argparse.ArgumentParser(
        prog="yt-scheduler",
        description="Daily video processing scheduler with autonomous discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  yt-scheduler --dry-run                    Preview what would be processed
  yt-scheduler --api-key KEY                Use YouTube API discovery + channel scraping
  yt-scheduler --config config.yaml         Use custom config file
  yt-scheduler --init-config                Generate default config and exit
  yt-scheduler -n 4                         Process 4 videos total
  yt-scheduler --channels @Niloya           Only scrape Niloya (no API discovery)
  yt-scheduler --discover-only              Only discover, don't process
  yt-scheduler --device cpu                 Force CPU processing

Discovery modes:
  With --api-key: YouTube Data API search + channel scraping (recommended)
  Without --api-key: Channel scraping only (legacy mode)
        """,
    )

    parser.add_argument(
        "-n",
        "--videos-per-run",
        type=int,
        default=None,
        help="Total videos to process this run (default: from config, usually 4)",
    )

    parser.add_argument(
        "--channels",
        nargs="+",
        default=None,
        help="Override channels to scrape (bypasses config whitelist)",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to discovery config YAML file",
    )

    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Generate default config file and exit",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="YouTube Data API key (or set YOUTUBE_API_KEY env var)",
    )

    parser.add_argument(
        "--max-risk",
        type=float,
        default=None,
        help="Override max copyright risk score (0.0-1.0)",
    )

    parser.add_argument(
        "--processed-file",
        type=Path,
        default=None,
        help="Path to processed videos tracking file",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output files",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be processed, don't actually process",
    )

    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Only discover and select videos, don't process them",
    )

    parser.add_argument(
        "--device",
        type=str,
        choices=["auto", "cpu", "cuda"],
        default=None,
        help="Processing device (default: from config)",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Demucs model name (default: from config)",
    )

    parser.add_argument(
        "--privacy",
        type=str,
        choices=["public", "unlisted", "private"],
        default=None,
        help="YouTube upload privacy (default: from config)",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress output except errors",
    )

    return parser


def main(args=None) -> int:
    """Main entry point for scheduler CLI."""
    try:
        parser = create_parser()
        parsed = parser.parse_args(args)

        setup_logger(verbose=parsed.verbose, quiet=parsed.quiet)

        # Handle --init-config
        if parsed.init_config:
            path = generate_default_config(parsed.config)
            print(f"Default config generated at: {path}")
            return 0

        # Load config
        config = load_config(parsed.config)

        # Apply CLI overrides to config
        if parsed.max_risk is not None:
            config.copyright.max_risk_score = parsed.max_risk

        # Resolve API key
        api_key = parsed.api_key or get_api_key()

        logger.info("Starting daily processing pipeline...")
        if api_key:
            logger.info("Mode: YouTube API discovery + channel scraping")
        else:
            logger.info("Mode: Channel scraping only (no API key)")
        logger.info(f"Videos per run: {parsed.videos_per_run or config.scheduler.videos_per_run}")

        count = run_daily_pipeline(
            config=config,
            channels=parsed.channels,
            processed_file=parsed.processed_file,
            output_dir=parsed.output_dir,
            videos_per_run=parsed.videos_per_run,
            dry_run=parsed.dry_run,
            discover_only=parsed.discover_only,
            device=parsed.device,
            model_name=parsed.model,
            privacy=parsed.privacy,
            api_key=api_key,
        )

        if parsed.dry_run or parsed.discover_only:
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
