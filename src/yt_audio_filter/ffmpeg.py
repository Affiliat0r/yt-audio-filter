"""FFmpeg wrapper functions for audio extraction and video remuxing."""

import json
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from .exceptions import FFmpegError, PrerequisiteError
from .ffmpeg_path import setup_ffmpeg_path
from .logger import get_logger

logger = get_logger()


def check_ffmpeg_available() -> bool:
    """
    Check if FFmpeg is available in the system PATH or bundled.

    This function first attempts to auto-detect and configure bundled FFmpeg
    before checking availability.

    Returns:
        True if FFmpeg is available, False otherwise
    """
    # Try to setup bundled FFmpeg if system FFmpeg not found
    setup_ffmpeg_path()

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def ensure_ffmpeg_available() -> None:
    """
    Ensure FFmpeg is available, raising an error if not.

    Raises:
        PrerequisiteError: If FFmpeg is not found
    """
    if not check_ffmpeg_available():
        raise PrerequisiteError(
            "FFmpeg not found",
            "Please install FFmpeg and ensure it's in your system PATH.\n"
            "  - Windows: Download from https://ffmpeg.org/download.html\n"
            "  - macOS: brew install ffmpeg\n"
            "  - Linux: sudo apt install ffmpeg (or equivalent)"
        )


def get_audio_info(file_path: Path) -> dict:
    """
    Get audio stream information from a file using ffprobe.

    Args:
        file_path: Path to the audio/video file

    Returns:
        Dictionary with sample_rate, channels, codec, duration
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,codec_name",
        "-show_entries", "format=duration",
        "-of", "json",
        str(file_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            logger.debug(f"ffprobe error: {result.stderr}")
            return {}

        data = json.loads(result.stdout)
        info = {}

        if data.get("streams") and len(data["streams"]) > 0:
            stream = data["streams"][0]
            info["sample_rate"] = int(stream.get("sample_rate", 44100))
            info["channels"] = int(stream.get("channels", 2))
            info["codec"] = stream.get("codec_name", "unknown")

        if data.get("format"):
            info["duration"] = float(data["format"].get("duration", 0))

        return info

    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        logger.debug(f"Failed to get audio info: {e}")
        return {}


def extract_audio(
    video_path: Path,
    output_path: Path,
    sample_rate: Optional[int] = None
) -> Path:
    """
    Extract audio from video file to WAV format.

    Args:
        video_path: Path to input video file
        output_path: Path for output WAV file
        sample_rate: Optional sample rate (None preserves original)

    Returns:
        Path to the extracted audio file

    Raises:
        FFmpegError: If extraction fails
    """
    logger.debug(f"Extracting audio from {video_path}")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",  # Overwrite output
        "-i", str(video_path),
        "-vn",  # No video
        "-acodec", "pcm_s16le",  # 16-bit PCM for WAV
    ]

    if sample_rate is not None:
        cmd.extend(["-ar", str(sample_rate)])

    cmd.append(str(output_path))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )

        if result.returncode != 0:
            raise FFmpegError(
                "Audio extraction failed",
                returncode=result.returncode,
                stderr=result.stderr
            )

        logger.debug(f"Audio extracted to {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        raise FFmpegError("Audio extraction timed out after 1 hour")
    except FileNotFoundError:
        raise PrerequisiteError("FFmpeg not found in system PATH")


def remux_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    audio_bitrate: str = "192k"
) -> Path:
    """
    Remux video with new audio track.

    The video stream is copied losslessly, and the audio is encoded as AAC.

    Args:
        video_path: Path to original video (for video stream)
        audio_path: Path to new audio file
        output_path: Path for output video
        audio_bitrate: Audio bitrate for AAC encoding

    Returns:
        Path to the remuxed video

    Raises:
        FFmpegError: If remuxing fails
    """
    logger.debug(f"Remuxing video with new audio")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",  # Overwrite output
        "-i", str(video_path),   # Input 0: original video
        "-i", str(audio_path),   # Input 1: new audio
        "-map", "0:v",           # Map video from input 0
        "-map", "1:a",           # Map audio from input 1
        "-c:v", "copy",          # Copy video losslessly
        "-c:a", "aac",           # Encode audio as AAC
        "-b:a", audio_bitrate,   # Audio bitrate
        "-shortest",             # Match shortest stream duration
        str(output_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )

        if result.returncode != 0:
            raise FFmpegError(
                "Video remuxing failed",
                returncode=result.returncode,
                stderr=result.stderr
            )

        logger.debug(f"Video remuxed to {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        raise FFmpegError("Video remuxing timed out after 1 hour")
    except FileNotFoundError:
        raise PrerequisiteError("FFmpeg not found in system PATH")
