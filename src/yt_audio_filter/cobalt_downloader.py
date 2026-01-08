"""Download YouTube videos using Cobalt API.

Cobalt is a free, open-source video download service that bypasses
YouTube's bot detection by proxying requests through their servers.
"""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from .exceptions import YouTubeDownloadError
from .logger import get_logger

logger = get_logger()

# Public Cobalt API instances (try multiple in case one is down)
# Source: https://instances.cobalt.best/
COBALT_API_URLS = [
    "https://cobalt-api.meowing.de",      # Score: 92%
    "https://cobalt-backend.canine.tools", # Score: 76%
    "https://kityune.imput.net",           # Score: 68%
    "https://capi.3kh0.net",               # Score: 68%
    "https://nachos.imput.net",            # Score: 64%
]


@dataclass
class CobaltVideoMetadata:
    """Metadata from a Cobalt download."""
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


def download_with_cobalt(
    url: str,
    output_dir: Path,
    video_quality: str = "1080",
    timeout: int = 300,
) -> CobaltVideoMetadata:
    """
    Download a YouTube video using Cobalt API.

    Args:
        url: YouTube video URL
        output_dir: Directory to save the downloaded video
        video_quality: Video quality (max, 1080, 720, etc.)
        timeout: Download timeout in seconds

    Returns:
        CobaltVideoMetadata with file path and basic info

    Raises:
        YouTubeDownloadError: If download fails
    """
    video_id = extract_video_id(url)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading {video_id} via Cobalt API...")

    # Request body - per Cobalt API docs
    # https://github.com/imputnet/cobalt/blob/main/docs/api.md
    request_body = json.dumps({
        "url": url,
        "videoQuality": video_quality,
        "youtubeVideoCodec": "h264",
        "downloadMode": "auto",
        "filenameStyle": "basic",
    }).encode("utf-8")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    download_url = None
    filename = None
    last_error = None

    # Try each Cobalt API instance
    for api_base in COBALT_API_URLS:
        try:
            # Ensure URL ends with / for root endpoint
            api_url = api_base.rstrip("/") + "/"
            logger.debug(f"Trying Cobalt API: {api_url}")

            req = Request(api_url, data=request_body, headers=headers, method="POST")

            with urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))

            status = result.get("status")
            logger.debug(f"Cobalt response status: {status}")
            logger.debug(f"Cobalt full response: {result}")

            if status == "error":
                error_obj = result.get("error", {})
                error_code = error_obj.get("code", "unknown") if isinstance(error_obj, dict) else str(error_obj)
                error_context = error_obj.get("context", {}) if isinstance(error_obj, dict) else {}
                logger.warning(f"Cobalt error: {error_code}, context: {error_context}")
                last_error = f"Cobalt API error: {error_code}"
                continue

            if status in ("tunnel", "redirect"):
                download_url = result.get("url")
                filename = result.get("filename", f"{video_id}.mp4")
                logger.info(f"Got download URL from {api_base}")
                break

            if status == "picker":
                # Multiple options - pick the first video
                picker = result.get("picker", [])
                if picker:
                    download_url = picker[0].get("url")
                    filename = f"{video_id}.mp4"
                    logger.info(f"Got picker URL from {api_base}")
                    break

            last_error = f"Unexpected Cobalt status: {status}"
            logger.warning(last_error)

        except HTTPError as e:
            # Try to read error response body for more details
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

    if not download_url:
        raise YouTubeDownloadError(
            "Failed to get download URL from Cobalt",
            last_error or "All Cobalt API instances failed"
        )

    # Download the actual video file
    logger.info(f"Downloading video from Cobalt tunnel...")
    output_path = output_dir / filename

    try:
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

                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        logger.debug(f"Download progress: {percent:.1f}%")

    except Exception as e:
        if output_path.exists():
            output_path.unlink()
        raise YouTubeDownloadError(f"Failed to download video file: {e}")

    # Ensure file has .mp4 extension
    if not output_path.suffix == ".mp4":
        new_path = output_path.with_suffix(".mp4")
        output_path.rename(new_path)
        output_path = new_path

    logger.info(f"Downloaded: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")

    return CobaltVideoMetadata(
        video_id=video_id,
        title=filename.rsplit(".", 1)[0] if filename else video_id,
        file_path=output_path,
    )


def get_video_metadata_yt_dlp(url: str) -> dict:
    """
    Get video metadata using yt-dlp (extraction only, no download).
    This usually doesn't trigger bot detection.
    """
    try:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "video_id": info.get("id", ""),
                "title": info.get("title", ""),
                "description": info.get("description", ""),
                "channel": info.get("channel", info.get("uploader", "")),
                "tags": info.get("tags", []) or [],
                "duration": info.get("duration", 0) or 0,
                "view_count": info.get("view_count", 0) or 0,
            }
    except Exception as e:
        logger.warning(f"Could not get metadata via yt-dlp: {e}")
        return {}
