"""Download YouTube videos using Invidious API.

Invidious is a free, open-source YouTube frontend
from GitHub: https://github.com/iv-org/invidious

It has stable public API instances that bypass YouTube's bot detection.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from .exceptions import YouTubeDownloadError
from .logger import get_logger

logger = get_logger()

# Public Invidious API instances
# Source: https://docs.invidious.io/instances/
INVIDIOUS_API_URLS = [
    "https://inv.nadeko.net",             # Chile
    "https://yewtu.be",                   # Germany
    "https://invidious.nerdvpn.de",       # Ukraine
    "https://invidious.privacydev.net",   # USA
    "https://vid.puffyan.us",             # USA
    "https://invidious.io.lol",           # Germany
    "https://inv.tux.pizza",              # Finland
]


@dataclass
class InvidiousVideoMetadata:
    """Metadata from an Invidious download."""
    video_id: str
    title: str
    file_path: Path


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def download_with_invidious(
    url: str,
    output_dir: Path,
    timeout: int = 300,
) -> InvidiousVideoMetadata:
    """
    Download a YouTube video using Invidious API.

    Args:
        url: YouTube video URL
        output_dir: Directory to save the downloaded video
        timeout: Download timeout in seconds

    Returns:
        InvidiousVideoMetadata with file path and basic info

    Raises:
        YouTubeDownloadError: If download fails
    """
    video_id = extract_video_id(url)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {video_id} via Invidious API...")

    video_info = None
    last_error = None

    # Try each Invidious API instance
    for api_base in INVIDIOUS_API_URLS:
        try:
            # Get video info from Invidious
            api_url = f"{api_base.rstrip('/')}/api/v1/videos/{video_id}"
            logger.debug(f"Trying Invidious API: {api_url}")

            req = Request(api_url, headers={"User-Agent": "yt-audio-filter/1.0"})

            with urlopen(req, timeout=30) as response:
                video_info = json.loads(response.read().decode("utf-8"))

            if video_info and "adaptiveFormats" in video_info:
                logger.info(f"Got video info from {api_base}")
                break

        except HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            last_error = f"HTTP {e.code} from {api_base}: {error_body[:200]}"
            logger.warning(last_error)
            continue
        except URLError as e:
            last_error = f"Connection error to {api_base}: {e.reason}"
            logger.warning(last_error)
            continue
        except Exception as e:
            last_error = f"Error with {api_base}: {e}"
            logger.warning(last_error)
            continue

    if not video_info:
        raise YouTubeDownloadError(
            "Failed to get video info from Invidious",
            last_error or "All Invidious API instances failed"
        )

    # Extract title and formats
    title = video_info.get("title", video_id)
    adaptive_formats = video_info.get("adaptiveFormats", [])

    if not adaptive_formats:
        raise YouTubeDownloadError(
            "No video formats found",
            "Invidious returned empty format list"
        )

    # Find best quality video format
    # Invidious provides both video-only and audio-only formats
    # We want a combined format if available, or highest quality video
    video_formats = [f for f in adaptive_formats if f.get("type", "").startswith("video/mp4")]

    if not video_formats:
        # Try any video format
        video_formats = [f for f in adaptive_formats if "video" in f.get("type", "")]

    if not video_formats:
        raise YouTubeDownloadError("No suitable video format found")

    # Sort by quality (bitrate) and pick highest
    video_formats.sort(key=lambda f: f.get("bitrate", 0), reverse=True)
    best_format = video_formats[0]

    download_url = best_format.get("url")
    if not download_url:
        raise YouTubeDownloadError("No download URL in Invidious response")

    logger.info(f"Downloading: {title}")
    logger.debug(f"Quality: {best_format.get('qualityLabel', 'unknown')}, Bitrate: {best_format.get('bitrate', 0)}")

    # Download video
    output_path = output_dir / f"{video_id}.mp4"

    try:
        logger.info("Downloading video...")
        req = Request(download_url, headers={"User-Agent": "yt-audio-filter/1.0"})

        with urlopen(req, timeout=timeout) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB chunks

            with open(output_path, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0 and downloaded % (10 * chunk_size) == 0:
                        percent = (downloaded / total_size) * 100
                        logger.debug(f"Download progress: {percent:.1f}%")

        logger.info(f"Downloaded: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")

    except Exception as e:
        if output_path.exists():
            output_path.unlink()
        raise YouTubeDownloadError(f"Failed to download video file: {e}")

    return InvidiousVideoMetadata(
        video_id=video_id,
        title=title,
        file_path=output_path,
    )


def get_video_metadata_invidious(url: str) -> dict:
    """
    Get video metadata using Invidious API.

    Args:
        url: YouTube video URL

    Returns:
        Dictionary with video metadata (title, description, etc.)
    """
    try:
        video_id = extract_video_id(url)

        # Try each Invidious API instance
        for api_base in INVIDIOUS_API_URLS:
            try:
                api_url = f"{api_base.rstrip('/')}/api/v1/videos/{video_id}"
                req = Request(api_url, headers={"User-Agent": "yt-audio-filter/1.0"})

                with urlopen(req, timeout=30) as response:
                    video_info = json.loads(response.read().decode("utf-8"))

                if video_info:
                    return {
                        "video_id": video_id,
                        "title": video_info.get("title", ""),
                        "description": video_info.get("description", ""),
                        "channel": video_info.get("author", ""),
                        "tags": video_info.get("keywords", []) or [],
                        "duration": video_info.get("lengthSeconds", 0) or 0,
                        "view_count": video_info.get("viewCount", 0) or 0,
                    }
            except:
                continue

        logger.warning("Could not get metadata from Invidious")
        return {}

    except Exception as e:
        logger.warning(f"Could not get metadata via Invidious: {e}")
        return {}
