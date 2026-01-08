"""YouTube video download integration using yt-dlp."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .exceptions import YouTubeDownloadError, PrerequisiteError, ValidationError
from .logger import get_logger

logger = get_logger()


@dataclass
class VideoMetadata:
    """Metadata from a downloaded YouTube video."""
    video_id: str
    title: str
    description: str
    channel: str
    tags: List[str]
    duration: int  # seconds
    view_count: int
    file_path: Path

# YouTube URL patterns
YOUTUBE_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+",
    r"(?:https?://)?(?:www\.)?youtu\.be/[\w-]+",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]+",
    r"(?:https?://)?(?:m\.)?youtube\.com/watch\?v=[\w-]+",
]


def is_youtube_url(input_str: str) -> bool:
    """
    Check if input string is a YouTube URL.

    Args:
        input_str: String to check

    Returns:
        True if input is a YouTube URL, False otherwise
    """
    if not input_str or not isinstance(input_str, str):
        return False

    input_str = input_str.strip()

    # Quick check: must contain youtube or youtu.be
    if "youtube" not in input_str.lower() and "youtu.be" not in input_str.lower():
        return False

    return any(re.match(pattern, input_str, re.IGNORECASE) for pattern in YOUTUBE_PATTERNS)


def validate_youtube_url(url: str) -> None:
    """
    Validate that a string is a valid YouTube URL.

    Args:
        url: URL string to validate

    Raises:
        ValidationError: If URL is not a valid YouTube URL
    """
    if not is_youtube_url(url):
        raise ValidationError(
            f"Invalid YouTube URL: {url}",
            "Supported formats:\n"
            "  - https://youtube.com/watch?v=VIDEO_ID\n"
            "  - https://youtu.be/VIDEO_ID\n"
            "  - https://youtube.com/shorts/VIDEO_ID",
        )


def check_ytdlp_available() -> bool:
    """
    Check if yt-dlp is available and importable.

    Returns:
        True if yt-dlp is available, False otherwise
    """
    try:
        import yt_dlp

        return True
    except ImportError:
        return False


def ensure_ytdlp_available() -> None:
    """
    Ensure yt-dlp is available, raising an error if not.

    Raises:
        PrerequisiteError: If yt-dlp is not installed
    """
    if not check_ytdlp_available():
        raise PrerequisiteError(
            "yt-dlp not installed",
            "Please install yt-dlp: pip install yt-dlp",
        )


def download_youtube_video(
    url: str,
    output_dir: Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> VideoMetadata:
    """
    Download a YouTube video as MP4 to the specified directory.

    Args:
        url: YouTube video URL
        output_dir: Directory to save the downloaded video
        progress_callback: Optional callback for progress updates.
            Called with dict containing 'status', 'percent', 'speed', 'eta'.

    Returns:
        VideoMetadata containing file path and original video info

    Raises:
        ValidationError: If URL is not a valid YouTube URL
        PrerequisiteError: If yt-dlp is not installed
        YouTubeDownloadError: If download fails
    """
    ensure_ytdlp_available()
    validate_youtube_url(url)

    import yt_dlp

    # Ensure output directory exists
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find ffmpeg location (bundled with project)
    project_root = Path(__file__).parent.parent.parent
    ffmpeg_dir = project_root / "ffmpeg-8.0.1-essentials_build" / "bin"
    ffmpeg_location = str(ffmpeg_dir) if ffmpeg_dir.exists() else None

    # Output template: use video ID for consistent naming
    output_template = str(output_dir / "%(id)s.%(ext)s")

    def _progress_hook(d: dict) -> None:
        """Internal progress hook that formats data for callback."""
        if progress_callback is None:
            return

        status = d.get("status", "unknown")
        percent = 0.0

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                percent = (downloaded / total) * 100

            progress_callback(
                {
                    "status": "downloading",
                    "percent": percent,
                    "speed": d.get("speed"),
                    "eta": d.get("eta"),
                }
            )
        elif status == "finished":
            progress_callback({"status": "finished", "percent": 100.0, "speed": None, "eta": None})

    ydl_opts = {
        # Format: download highest quality video + audio streams, merge with ffmpeg
        # This ensures we get the best available resolution (1080p, 4K, etc.)
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        # Ensure output is MP4 when merging is needed
        "merge_output_format": "mp4",
        # Output path template
        "outtmpl": output_template,
        # Single video only, no playlists
        "noplaylist": True,
        # Progress tracking
        "progress_hooks": [_progress_hook],
        # Retry settings for reliability
        "retries": 10,
        "fragment_retries": 10,
        # Reduce console noise (we use our own logging)
        "quiet": True,
        "no_warnings": True,
        # Enable Node.js runtime for YouTube JS challenge solving
        # Required for POT provider to work on GitHub Actions
        # Note: bgutil-ytdlp-pot-provider v1.0.0+ uses new extractor args syntax
        "extractor_args": {
            # POT server URL for bot detection bypass (bgutil-ytdlp-pot-provider v1.0.0+ syntax)
            # disable_innertube=1 restores legacy behavior and helps trigger POT usage
            "youtubepot-bgutilhttp": {
                "base_url": ["http://127.0.0.1:4416"],
                "disable_innertube": ["1"],
            }
        },
        # Explicitly use Node.js for JavaScript challenge solving (needed for POT provider)
        "js_runtimes": "node",
    }

    # Add ffmpeg location if found
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location
        logger.debug(f"Using ffmpeg from: {ffmpeg_location}")

    # Check for cookie file to bypass bot detection
    # Look in common locations for cookies.txt (Netscape format)
    cookie_locations = [
        Path.cwd() / "cookies.txt",
        project_root / "cookies.txt",
        Path.home() / ".yt-dlp" / "cookies.txt",
    ]
    for cookie_file in cookie_locations:
        if cookie_file.exists():
            ydl_opts["cookiefile"] = str(cookie_file)
            logger.debug(f"Using cookie file: {cookie_file}")
            break

    try:
        logger.info(f"Downloading from YouTube: {url}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info and download
            info = ydl.extract_info(url, download=True)

            if info is None:
                raise YouTubeDownloadError(
                    "Failed to extract video information",
                    "The video may be unavailable or restricted.",
                )

            video_id = info.get("id", "unknown")
            video_title = info.get("title", "Unknown")
            video_description = info.get("description", "")
            channel = info.get("channel", info.get("uploader", "Unknown"))
            tags = info.get("tags", []) or []
            duration = info.get("duration", 0) or 0
            view_count = info.get("view_count", 0) or 0

            logger.debug(f"Video title: {video_title}")
            logger.debug(f"Video ID: {video_id}")
            logger.debug(f"Channel: {channel}")
            logger.debug(f"Tags: {tags[:5]}...")  # Log first 5 tags

            # Find the downloaded file
            # yt-dlp may merge streams, so the extension might change
            downloaded_file = output_dir / f"{video_id}.mp4"

            if not downloaded_file.exists():
                # Try other common extensions
                for ext in ["mkv", "webm", "mp4"]:
                    alt_file = output_dir / f"{video_id}.{ext}"
                    if alt_file.exists():
                        downloaded_file = alt_file
                        break

            if not downloaded_file.exists():
                raise YouTubeDownloadError(
                    "Download completed but output file not found",
                    f"Expected file at: {downloaded_file}",
                )

            logger.info(f"Downloaded: {downloaded_file.name} ({video_title})")

            return VideoMetadata(
                video_id=video_id,
                title=video_title,
                description=video_description,
                channel=channel,
                tags=tags,
                duration=duration,
                view_count=view_count,
                file_path=downloaded_file,
            )

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        # Check if this is bot detection - if so, try Cobalt fallback
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            logger.warning("Bot detection triggered, trying Cobalt fallback...")
            return _download_with_cobalt_fallback(url, output_dir, progress_callback)

        # Provide friendlier error messages for common issues
        if "Private video" in error_msg:
            raise YouTubeDownloadError("Cannot download private video", error_msg)
        elif "Video unavailable" in error_msg:
            raise YouTubeDownloadError("Video is unavailable", error_msg)
        elif "age" in error_msg.lower():
            raise YouTubeDownloadError(
                "Video is age-restricted",
                "Age-restricted videos require authentication.",
            )
        else:
            raise YouTubeDownloadError("YouTube download failed", error_msg)

    except Exception as e:
        if isinstance(e, (YouTubeDownloadError, PrerequisiteError, ValidationError)):
            raise
        raise YouTubeDownloadError(f"Unexpected download error: {e}")


def _download_with_cobalt_fallback(
    url: str,
    output_dir: Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> VideoMetadata:
    """
    Fallback download using Cobalt API when yt-dlp fails due to bot detection.
    """
    from .cobalt_downloader import download_with_cobalt, get_video_metadata_yt_dlp

    # Try to get metadata first (usually works even when download is blocked)
    metadata = get_video_metadata_yt_dlp(url)

    if progress_callback:
        progress_callback({"status": "downloading", "percent": 10, "speed": None, "eta": None})

    # Download via Cobalt
    cobalt_result = download_with_cobalt(url, output_dir)

    if progress_callback:
        progress_callback({"status": "finished", "percent": 100, "speed": None, "eta": None})

    # Merge metadata
    return VideoMetadata(
        video_id=cobalt_result.video_id,
        title=metadata.get("title", cobalt_result.title),
        description=metadata.get("description", ""),
        channel=metadata.get("channel", "Unknown"),
        tags=metadata.get("tags", []),
        duration=metadata.get("duration", 0),
        view_count=metadata.get("view_count", 0),
        file_path=cobalt_result.file_path,
    )
