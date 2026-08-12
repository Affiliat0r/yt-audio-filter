"""
YT Audio Filter - Remove background music from videos using AI.

A CLI tool that leverages Facebook's Demucs AI model to isolate vocals
and remove background music from MP4 video files.
"""

__version__ = "1.0.0"
__author__ = "Product Team"

from .exceptions import (
    DemucsError,
    FFmpegError,
    PrerequisiteError,
    ValidationError,
    YouTubeDownloadError,
    YTAudioFilterError,
)
from .ffmpeg_path import setup_ffmpeg_path
from .youtube import download_youtube_video, is_youtube_url


def __getattr__(name: str):
    """Load `process_video` on first use rather than at import (PEP 562).

    Importing it eagerly pulled `pipeline` -> `demucs_processor` -> `torch`,
    so *any* import from this package — even `cartoon_catalog` — dragged in
    PyTorch. That made a ~5.4 GB install mandatory on every machine running a
    worker, when only music removal actually needs Demucs; overlay renders and
    search need nothing heavier than yt-dlp and FFmpeg.

    `from yt_audio_filter import process_video` still works exactly as before.
    """
    if name == "process_video":
        from .pipeline import process_video

        return process_video
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "process_video",
    "download_youtube_video",
    "is_youtube_url",
    "setup_ffmpeg_path",
    "YTAudioFilterError",
    "ValidationError",
    "FFmpegError",
    "DemucsError",
    "PrerequisiteError",
    "YouTubeDownloadError",
    "__version__",
]
