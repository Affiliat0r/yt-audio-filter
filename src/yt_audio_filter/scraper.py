"""YouTube channel/playlist scraper using yt-dlp."""

import argparse
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from .exceptions import YTAudioFilterError
from .logger import get_logger, setup_logger

logger = get_logger()

# Fix Windows console encoding for Unicode
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


class ScraperError(YTAudioFilterError):
    """Scraper-related errors."""
    pass


@dataclass
class VideoInfo:
    """Basic video information from scraping."""
    video_id: str
    title: str
    url: str
    duration: int  # seconds
    view_count: int
    upload_date: str  # YYYYMMDD format
    thumbnail_url: str  # YouTube thumbnail URL


def get_channel_videos(
    channel_url: str,
    max_videos: Optional[int] = None,
    include_shorts: bool = False,
) -> Iterator[VideoInfo]:
    """
    Extract video information from a YouTube channel.

    Args:
        channel_url: YouTube channel URL or handle (e.g., @Niloya, /c/Niloya, channel ID)
        max_videos: Maximum number of videos to fetch (None = all)
        include_shorts: Whether to include YouTube Shorts

    Yields:
        VideoInfo objects for each video

    Raises:
        ScraperError: If extraction fails
    """
    try:
        import yt_dlp
    except ImportError:
        raise ScraperError(
            "yt-dlp not installed",
            "Install with: pip install yt-dlp"
        )

    # Normalize channel URL
    if not channel_url.startswith(("http://", "https://")):
        # Handle @username format
        if channel_url.startswith("@"):
            channel_url = f"https://www.youtube.com/{channel_url}"
        else:
            channel_url = f"https://www.youtube.com/@{channel_url}"

    # Ensure we're getting the videos tab
    if "/videos" not in channel_url:
        channel_url = channel_url.rstrip("/") + "/videos"

    logger.info(f"Scraping channel: {channel_url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,  # Don't download, just get metadata
        "ignoreerrors": True,  # Skip unavailable videos
        # Get original Turkish titles
        "extractor_args": {"youtube": {"hl": ["tr"]}},
        "geo_bypass_country": "TR",  # Simulate being in Turkey
        "http_headers": {
            "Accept-Language": "tr-TR,tr;q=0.9",
        },
    }

    if max_videos:
        ydl_opts["playlistend"] = max_videos

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)

            if info is None:
                raise ScraperError(
                    "Failed to extract channel information",
                    "The channel may not exist or is unavailable."
                )

            entries = info.get("entries", [])
            if not entries:
                logger.warning("No videos found in channel")
                return

            count = 0
            for entry in entries:
                if entry is None:
                    continue

                video_id = entry.get("id", "")
                title = entry.get("title", "Unknown")
                duration = entry.get("duration") or 0

                # Skip shorts if not included (shorts are typically < 60 seconds)
                if not include_shorts and duration > 0 and duration < 60:
                    logger.debug(f"Skipping short: {title}")
                    continue

                url = f"https://www.youtube.com/watch?v={video_id}"

                # Get best available thumbnail
                thumbnail = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

                yield VideoInfo(
                    video_id=video_id,
                    title=title,
                    url=url,
                    duration=duration,
                    view_count=entry.get("view_count") or 0,
                    upload_date=entry.get("upload_date") or "",
                    thumbnail_url=thumbnail,
                )

                count += 1
                if max_videos and count >= max_videos:
                    break

            logger.info(f"Found {count} videos")

    except yt_dlp.utils.DownloadError as e:
        raise ScraperError(f"Failed to scrape channel: {e}")
    except Exception as e:
        if isinstance(e, ScraperError):
            raise
        raise ScraperError(f"Unexpected error: {e}")


def scrape_to_file(
    channel_url: str,
    output_file: Path,
    max_videos: Optional[int] = None,
    format: str = "urls",
    include_shorts: bool = False,
) -> int:
    """
    Scrape channel videos and save to file.

    Args:
        channel_url: YouTube channel URL
        output_file: Output file path
        max_videos: Maximum videos to scrape
        format: Output format ("urls", "json", "csv")
        include_shorts: Whether to include shorts

    Returns:
        Number of videos scraped
    """
    videos = list(get_channel_videos(channel_url, max_videos, include_shorts))

    if not videos:
        logger.warning("No videos to save")
        return 0

    output_file.parent.mkdir(parents=True, exist_ok=True)

    if format == "urls":
        # Simple URL list, one per line
        with open(output_file, "w", encoding="utf-8") as f:
            for video in videos:
                f.write(f"{video.url}\n")

    elif format == "json":
        # Full metadata as JSON
        data = [
            {
                "video_id": v.video_id,
                "title": v.title,
                "url": v.url,
                "duration": v.duration,
                "view_count": v.view_count,
                "upload_date": v.upload_date,
                "thumbnail_url": v.thumbnail_url,
            }
            for v in videos
        ]
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    elif format == "csv":
        # CSV format
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("video_id,title,url,duration,view_count,upload_date,thumbnail_url\n")
            for v in videos:
                # Escape quotes in title
                title = v.title.replace('"', '""')
                f.write(f'"{v.video_id}","{title}","{v.url}",{v.duration},{v.view_count},"{v.upload_date}","{v.thumbnail_url}"\n')

    else:
        raise ScraperError(f"Unknown format: {format}")

    logger.info(f"Saved {len(videos)} videos to {output_file}")
    return len(videos)


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for scraper CLI."""
    parser = argparse.ArgumentParser(
        prog="yt-channel-scrape",
        description="Scrape video URLs and titles from a YouTube channel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  yt-channel-scrape @Niloya                      Scrape all videos from Niloya channel
  yt-channel-scrape @Niloya -n 50                Scrape first 50 videos
  yt-channel-scrape @Niloya -o niloya.txt        Save URLs to file
  yt-channel-scrape @Niloya -f json -o data.json Export as JSON
  yt-channel-scrape @Niloya --shorts             Include YouTube Shorts

Output Formats:
  urls  - One URL per line (default, ready for batch processing)
  json  - Full metadata as JSON array
  csv   - Comma-separated values with headers
        """
    )

    parser.add_argument(
        "channel",
        type=str,
        help="YouTube channel URL, handle (@username), or name"
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path (default: print to stdout)"
    )

    parser.add_argument(
        "-n", "--max-videos",
        type=int,
        default=None,
        help="Maximum number of videos to scrape (default: all)"
    )

    parser.add_argument(
        "-f", "--format",
        type=str,
        choices=["urls", "json", "csv"],
        default="urls",
        help="Output format (default: urls)"
    )

    parser.add_argument(
        "--shorts",
        action="store_true",
        help="Include YouTube Shorts (excluded by default)"
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
    """Main entry point for scraper CLI."""
    try:
        parser = create_parser()
        parsed = parser.parse_args(args)

        # Setup logging
        setup_logger(verbose=parsed.verbose, quiet=parsed.quiet)

        if parsed.output:
            # Save to file
            count = scrape_to_file(
                channel_url=parsed.channel,
                output_file=parsed.output,
                max_videos=parsed.max_videos,
                format=parsed.format,
                include_shorts=parsed.shorts,
            )
            if not parsed.quiet:
                print(f"Scraped {count} videos to {parsed.output}")
        else:
            # Print to stdout
            videos = list(get_channel_videos(
                channel_url=parsed.channel,
                max_videos=parsed.max_videos,
                include_shorts=parsed.shorts,
            ))

            if parsed.format == "urls":
                for video in videos:
                    print(video.url)
            elif parsed.format == "json":
                data = [
                    {
                        "video_id": v.video_id,
                        "title": v.title,
                        "url": v.url,
                        "duration": v.duration,
                        "view_count": v.view_count,
                        "upload_date": v.upload_date,
                        "thumbnail_url": v.thumbnail_url,
                    }
                    for v in videos
                ]
                print(json.dumps(data, indent=2, ensure_ascii=False))
            elif parsed.format == "csv":
                print("video_id,title,url,duration,view_count,upload_date,thumbnail_url")
                for v in videos:
                    title = v.title.replace('"', '""')
                    print(f'"{v.video_id}","{title}","{v.url}",{v.duration},{v.view_count},"{v.upload_date}","{v.thumbnail_url}"')

        return 0

    except ScraperError as e:
        print(f"Error: {e}", file=sys.stderr)
        if e.details:
            print(f"Details: {e.details}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        print("\nCancelled by user", file=sys.stderr)
        return 130

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
