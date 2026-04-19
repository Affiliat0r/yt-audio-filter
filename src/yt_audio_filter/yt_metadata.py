"""Fetch lightweight YouTube metadata (title, channel, description, tags) without downloading."""

from dataclasses import dataclass, field
from typing import List, Optional

from .exceptions import YouTubeDownloadError
from .logger import get_logger
from .youtube import ensure_ytdlp_available, validate_youtube_url

logger = get_logger()


@dataclass
class YouTubeMetadata:
    video_id: str
    title: str
    channel: str
    uploader: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    duration: int = 0


def fetch_yt_metadata(url: str) -> YouTubeMetadata:
    """Fetch a YouTube video's metadata without downloading the media."""
    ensure_ytdlp_available()
    validate_youtube_url(url)

    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {"player_client": ["tv_embedded", "ios", "web_embedded", "android"]},
            # Neutralize bgutil script mode's slow Deno cold-start (see
            # youtube.download_stream for the same guard).
            "youtubepot-bgutilscript": {"script_path": ["__disabled__"]},
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            raise YouTubeDownloadError(f"yt-dlp returned no info for {url}")
    except Exception as e:
        if isinstance(e, YouTubeDownloadError):
            raise
        raise YouTubeDownloadError(f"Failed to fetch metadata for {url}: {e}")

    return YouTubeMetadata(
        video_id=info.get("id", "unknown"),
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        uploader=info.get("uploader") or "",
        description=info.get("description") or "",
        tags=info.get("tags") or [],
        duration=int(info.get("duration") or 0),
    )
