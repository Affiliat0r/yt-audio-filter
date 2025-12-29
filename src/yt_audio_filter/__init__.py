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
from .pipeline import process_video
from .youtube import download_youtube_video, is_youtube_url

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
