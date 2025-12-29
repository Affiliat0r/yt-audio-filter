"""FFmpeg path management - auto-detect and configure bundled FFmpeg."""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger()

# Cache for FFmpeg path to avoid repeated lookups
_ffmpeg_path: Optional[Path] = None
_setup_done: bool = False


def find_bundled_ffmpeg() -> Optional[Path]:
    """
    Find bundled FFmpeg installation relative to the package.

    Returns:
        Path to FFmpeg bin directory if found, None otherwise
    """
    # Get the package root directory (yt-audio-filter/)
    package_dir = Path(__file__).parent.parent.parent

    # Common locations for bundled FFmpeg
    search_paths = [
        package_dir / "ffmpeg-8.0.1-essentials_build" / "bin",
        package_dir / "ffmpeg-8.0.1-full_build" / "bin",
        package_dir / "ffmpeg" / "bin",
        package_dir / "bin",
        # Also check one level up (in case package is installed differently)
        package_dir.parent / "ffmpeg-8.0.1-essentials_build" / "bin",
        package_dir.parent / "ffmpeg" / "bin",
    ]

    for ffmpeg_dir in search_paths:
        # Check for Windows executable
        if (ffmpeg_dir / "ffmpeg.exe").exists():
            return ffmpeg_dir
        # Check for Unix executable
        if (ffmpeg_dir / "ffmpeg").exists():
            return ffmpeg_dir

    return None


def setup_ffmpeg_path() -> bool:
    """
    Configure FFmpeg in PATH if not already available.

    This function checks if FFmpeg is accessible and if not,
    attempts to find and add a bundled FFmpeg to the PATH.

    Returns:
        True if FFmpeg is available (either system or bundled), False otherwise
    """
    global _ffmpeg_path, _setup_done

    if _setup_done:
        return _ffmpeg_path is not None or shutil.which("ffmpeg") is not None

    _setup_done = True

    # First check if FFmpeg is already in PATH
    if shutil.which("ffmpeg"):
        logger.debug("FFmpeg found in system PATH")
        return True

    # Try to find bundled FFmpeg
    bundled_dir = find_bundled_ffmpeg()
    if bundled_dir:
        # Add to PATH for this process and any subprocesses
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bundled_dir) + os.pathsep + current_path
        _ffmpeg_path = bundled_dir
        logger.debug(f"Added bundled FFmpeg to PATH: {bundled_dir}")
        return True

    logger.debug("No FFmpeg found (system or bundled)")
    return False


def get_ffmpeg_path() -> Optional[Path]:
    """
    Get the path to FFmpeg binary.

    Returns:
        Path to ffmpeg executable, or None if not found
    """
    setup_ffmpeg_path()

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return Path(ffmpeg)
    return None


def get_ffprobe_path() -> Optional[Path]:
    """
    Get the path to ffprobe binary.

    Returns:
        Path to ffprobe executable, or None if not found
    """
    setup_ffmpeg_path()

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return Path(ffprobe)
    return None
