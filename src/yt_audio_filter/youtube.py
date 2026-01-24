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


def extract_video_id(url: str) -> str:
    """
    Extract the video ID from a YouTube URL without downloading.

    Args:
        url: YouTube video URL

    Returns:
        Video ID string

    Raises:
        ValidationError: If URL is not a valid YouTube URL
        YouTubeDownloadError: If video ID extraction fails
    """
    ensure_ytdlp_available()
    validate_youtube_url(url)

    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise YouTubeDownloadError("Failed to extract video information")
            return info.get("id", "unknown")
    except Exception as e:
        # Fallback: try to extract from URL pattern
        match = re.search(r"(?:v=|/shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
        if match:
            return match.group(1)
        raise YouTubeDownloadError(f"Failed to extract video ID: {e}")


def download_youtube_video(
    url: str,
    output_dir: Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
    use_cache: bool = True,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    gui_exe_path: Optional[Path] = None,
) -> VideoMetadata:
    """
    Download a YouTube video using GUI automation (YoutubeDownloader.exe).

    If the video has already been downloaded to the cache directory, it will
    be reused instead of re-downloading.

    Args:
        url: YouTube video URL
        output_dir: Directory to save the downloaded video
        progress_callback: Optional callback for progress updates.
            Called with dict containing 'status', 'percent', 'speed', 'eta'.
        use_cache: If True, check cache and skip download if already exists (default: True)
        cookies_from_browser: Browser to extract cookies from (chrome, firefox, edge, etc.) [IGNORED]
        proxy: Proxy URL [IGNORED]
        gui_exe_path: Path to YoutubeDownloader.exe for GUI automation

    Returns:
        VideoMetadata containing file path and original video info

    Raises:
        ValidationError: If URL is not a valid YouTube URL
        YouTubeDownloadError: If download fails
    """
    validate_youtube_url(url)

    from .gui_downloader import download_with_gui
    from .invidious_downloader import get_video_metadata_invidious

    # Ensure output directory exists
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if video already exists in cache
    if use_cache:
        try:
            video_id = extract_video_id(url)

            # Check for existing file with common extensions
            for ext in ["mp4", "mkv", "webm"]:
                cached_file = output_dir / f"{video_id}.{ext}"
                if cached_file.exists() and cached_file.stat().st_size > 0:
                    logger.info(f"Using cached video: {cached_file.name} (skipping download)")

                    # Try to get metadata from Invidious
                    metadata = get_video_metadata_invidious(url)
                    if metadata:
                        return VideoMetadata(
                            video_id=metadata.get("video_id", video_id),
                            title=metadata.get("title", cached_file.stem),
                            description=metadata.get("description", ""),
                            channel=metadata.get("channel", "Unknown"),
                            tags=metadata.get("tags", []),
                            duration=metadata.get("duration", 0),
                            view_count=metadata.get("view_count", 0),
                            file_path=cached_file,
                        )
                    else:
                        # Fallback to filename
                        return VideoMetadata(
                            video_id=video_id,
                            title=cached_file.stem,
                            description="",
                            channel="Unknown",
                            tags=[],
                            duration=0,
                            view_count=0,
                            file_path=cached_file,
                        )
        except Exception as e:
            logger.debug(f"Cache check failed, proceeding with download: {e}")

    # Download using GUI automation
    logger.info(f"Downloading from YouTube using GUI automation: {url}")

    try:
        # Use GUI automation to download
        gui_result = download_with_gui(
            url=url,
            output_dir=output_dir,
            exe_path=gui_exe_path,
            timeout=600  # 10 minutes
        )

        # Try to get metadata from Invidious for better info
        metadata = get_video_metadata_invidious(url)
        if metadata:
            video_id = metadata.get("video_id", extract_video_id(url))
            return VideoMetadata(
                video_id=video_id,
                title=metadata.get("title", gui_result.title),
                description=metadata.get("description", ""),
                channel=metadata.get("channel", "Unknown"),
                tags=metadata.get("tags", []),
                duration=metadata.get("duration", 0),
                view_count=metadata.get("view_count", 0),
                file_path=gui_result.video_path,
            )
        else:
            # Use GUI result title and extract video ID from URL
            video_id = extract_video_id(url)
            return VideoMetadata(
                video_id=video_id,
                title=gui_result.title,
                description="",
                channel="Unknown",
                tags=[],
                duration=0,
                view_count=0,
                file_path=gui_result.video_path,
            )
    except Exception as e:
        if isinstance(e, YouTubeDownloadError):
            raise
        raise YouTubeDownloadError(f"GUI download failed: {e}")


