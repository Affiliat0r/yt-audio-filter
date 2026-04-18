"""YouTube video download integration using yt-dlp."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Literal, Optional

from .exceptions import YouTubeDownloadError, PrerequisiteError, ValidationError
from .logger import get_logger

StreamMode = Literal["video-only", "audio-only", "video+audio"]

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
    Download a YouTube video using YTDownloader GUI automation.

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
        gui_exe_path: Path to YTDownloader.exe for GUI automation

    Returns:
        VideoMetadata containing file path and original video info

    Raises:
        ValidationError: If URL is not a valid YouTube URL
        YouTubeDownloadError: If download fails
    """
    validate_youtube_url(url)

    from .ytdownloader import download_with_ytdownloader
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

    # Download using YTDownloader GUI automation
    logger.info(f"Downloading from YouTube using YTDownloader: {url}")

    try:
        # Use YTDownloader GUI automation to download
        yt_result = download_with_ytdownloader(
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
                title=metadata.get("title", yt_result.title),
                description=metadata.get("description", ""),
                channel=metadata.get("channel", "Unknown"),
                tags=metadata.get("tags", []),
                duration=metadata.get("duration", 0),
                view_count=metadata.get("view_count", 0),
                file_path=yt_result.video_path,
            )
        else:
            # Use YTDownloader result title and extract video ID from URL
            video_id = extract_video_id(url)
            return VideoMetadata(
                video_id=video_id,
                title=yt_result.title,
                description="",
                channel="Unknown",
                tags=[],
                duration=0,
                view_count=0,
                file_path=yt_result.video_path,
            )
    except Exception as e:
        if isinstance(e, YouTubeDownloadError):
            raise
        raise YouTubeDownloadError(f"YTDownloader download failed: {e}")


_STREAM_FORMAT_MAP = {
    # Final `/18` or `/b` fallbacks are combined formats that YouTube still
    # serves without PO Tokens — we'll post-extract the wanted stream.
    "video-only": "bestvideo[ext=mp4]/bestvideo/18/b",
    "audio-only": "bestaudio[ext=m4a]/bestaudio/18/b",
    "video+audio": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best/18",
}
_STREAM_PREFIX = {
    "video-only": "video",
    "audio-only": "audio",
    "video+audio": "full",
}


def _extract_stream_with_ffmpeg(source: Path, dest: Path, mode: StreamMode) -> Path:
    """Extract video-only or audio-only stream from a full media file via FFmpeg copy."""
    import subprocess
    from .ffmpeg import ensure_ffmpeg_available
    from .exceptions import FFmpegError

    ensure_ffmpeg_available()

    dest.parent.mkdir(parents=True, exist_ok=True)
    if mode == "video-only":
        map_args = ["-map", "0:v:0", "-an"]
    elif mode == "audio-only":
        map_args = ["-map", "0:a:0", "-vn"]
    else:
        raise YouTubeDownloadError(f"Unsupported extraction mode: {mode}")

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(source),
        *map_args,
        "-c", "copy",
        str(dest),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600
    )
    if result.returncode != 0:
        raise FFmpegError(
            f"Stream extraction ({mode}) failed",
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return dest


def download_stream(
    url: str,
    output_dir: Path,
    mode: StreamMode,
    cookies_from_browser: Optional[str] = None,
    proxy: Optional[str] = None,
    use_cache: bool = True,
) -> Path:
    """Download a specific stream (video-only/audio-only/video+audio) from a YouTube URL.

    Uses yt-dlp directly with format selection — no GUI automation. Bandwidth
    savings are significant when only one stream is needed.

    Cache naming: `<prefix>_<video_id>.<ext>` so video-only and audio-only
    downloads of the same URL don't clash.
    """
    ensure_ytdlp_available()
    validate_youtube_url(url)

    import yt_dlp

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    video_id = extract_video_id(url)
    prefix = _STREAM_PREFIX[mode]

    if use_cache:
        for ext in ("mp4", "m4a", "webm", "mkv", "opus"):
            candidate = output_dir / f"{prefix}_{video_id}.{ext}"
            if candidate.exists() and candidate.stat().st_size > 0:
                logger.info(f"Using cached {mode} download: {candidate.name}")
                return candidate

    output_template = str(output_dir / f"{prefix}_%(id)s.%(ext)s")
    ydl_opts: dict = {
        "format": _STREAM_FORMAT_MAP[mode],
        "outtmpl": output_template,
        "quiet": False,
        "no_warnings": False,
        "noprogress": False,
        "merge_output_format": "mp4" if mode == "video+audio" else None,
        # Client cascade: yt-dlp tries in order, picks the first that yields
        # usable formats. `ios`/`tv_embedded`/`web_embedded` avoid the
        # n-challenge JS deobfuscation that fails without Deno/Node.
        # Shorts often need a non-iOS client because iOS requires PO tokens.
        "extractor_args": {
            "youtube": {"player_client": ["tv_embedded", "ios", "web_embedded", "android"]}
        },
    }
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if proxy:
        ydl_opts["proxy"] = proxy
    ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}

    logger.info(f"Downloading {mode} stream from YouTube: {url}")

    ytdlp_error: Optional[Exception] = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise YouTubeDownloadError(f"yt-dlp returned no info for {url}")
            downloaded = ydl.prepare_filename(info)

        result_path = Path(downloaded)
        if not result_path.exists():
            for ext in ("mp4", "m4a", "webm", "mkv", "opus"):
                fallback = output_dir / f"{prefix}_{video_id}.{ext}"
                if fallback.exists():
                    result_path = fallback
                    break
            else:
                raise YouTubeDownloadError(
                    f"yt-dlp reported success but no file at expected path: {downloaded}"
                )

        # If yt-dlp fell back to a combined format (e.g. 18) for a stream-only
        # request, strip the unneeded stream so downstream stages see a clean
        # video-only or audio-only file.
        if mode in ("video-only", "audio-only"):
            from .ffmpeg import get_audio_info
            import subprocess as _sp

            probe = _sp.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "stream=codec_type",
                    "-of", "default=nw=1",
                    str(result_path),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            stream_types = {line.split("=", 1)[-1].strip() for line in probe.stdout.splitlines()}
            wanted_only = "video" if mode == "video-only" else "audio"
            needs_strip = (
                ("video" in stream_types and "audio" in stream_types)
                or wanted_only not in stream_types
            )
            if needs_strip and wanted_only in stream_types:
                desired_ext = "mp4" if mode == "video-only" else "m4a"
                # Temp file keeps the real extension so ffmpeg can infer format;
                # dot-prefix marks it as in-flight.
                stripped = output_dir / f".strip_{prefix}_{video_id}.{desired_ext}"
                try:
                    _extract_stream_with_ffmpeg(result_path, stripped, mode)
                except Exception as strip_err:
                    logger.warning(f"Post-download stream strip failed: {strip_err}")
                else:
                    final = output_dir / f"{prefix}_{video_id}.{desired_ext}"
                    if final.exists() and final != result_path:
                        final.unlink()
                    stripped.replace(final)
                    if result_path != final and result_path.exists():
                        try:
                            result_path.unlink()
                        except OSError:
                            pass
                    result_path = final
                    logger.info(f"Extracted {mode} from combined download -> {final.name}")

        logger.info(f"Downloaded {mode}: {result_path.name}")
        return result_path

    except Exception as e:
        ytdlp_error = e
        logger.warning(f"yt-dlp stream-selective download failed: {e}")
        # Clean up any partial file so the fallback doesn't see stale state
        for ext in ("mp4", "m4a", "webm", "mkv", "opus"):
            partial = output_dir / f"{prefix}_{video_id}.{ext}.part"
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
            stale = output_dir / f"{prefix}_{video_id}.{ext}"
            if stale.exists() and stale.stat().st_size == 0:
                try:
                    stale.unlink()
                except OSError:
                    pass

    # Fallback: use the existing robust download chain (GUI automation etc.),
    # then extract the requested stream with FFmpeg -c copy.
    if mode == "video+audio":
        logger.info("Falling back to full-video download chain for video+audio request")
        full_meta = download_youtube_video(
            url=url,
            output_dir=output_dir,
            cookies_from_browser=cookies_from_browser,
            proxy=proxy,
        )
        return full_meta.file_path

    logger.info(f"Falling back: full download via existing chain, then extract {mode}")
    full_output_dir = output_dir / "_full"
    full_meta = download_youtube_video(
        url=url,
        output_dir=full_output_dir,
        cookies_from_browser=cookies_from_browser,
        proxy=proxy,
    )
    dest_ext = "mp4" if mode == "video-only" else "m4a"
    dest = output_dir / f"{prefix}_{video_id}.{dest_ext}"
    try:
        _extract_stream_with_ffmpeg(full_meta.file_path, dest, mode)
    except Exception as extract_err:
        raise YouTubeDownloadError(
            f"Both yt-dlp and fallback extraction failed for {url}",
            f"yt-dlp: {ytdlp_error}\nextract: {extract_err}",
        )
    logger.info(f"Extracted {mode} stream to {dest.name}")
    return dest
