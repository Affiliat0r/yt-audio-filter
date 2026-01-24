"""FFmpeg wrapper functions for audio extraction and video remuxing."""

import json
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

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
            encoding='utf-8',
            errors='replace',
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
            encoding='utf-8',
            errors='replace',
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
            encoding='utf-8',
            errors='replace',  # Replace invalid characters instead of crashing
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


def check_nvenc_available() -> bool:
    """Check if NVIDIA NVENC encoder is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def remux_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    audio_bitrate: str = "192k",
    watermark: bool = False
) -> Path:
    """
    Remux video with new audio track.

    The video stream is copied losslessly (unless watermark is enabled),
    and the audio is encoded as AAC.

    Args:
        video_path: Path to original video (for video stream)
        audio_path: Path to new audio file
        output_path: Path for output video
        audio_bitrate: Audio bitrate for AAC encoding
        watermark: Add visual modifications to help avoid Content ID

    Returns:
        Path to the remuxed video

    Raises:
        FFmpegError: If remuxing fails
    """
    logger.debug(f"Remuxing video with new audio (watermark={watermark})")

    if watermark:
        # Apply aggressive transformations to evade Content ID:
        # 1. Speed change (1.05x) - changes temporal fingerprint
        # 2. Border/PiP - changes frame composition  
        # 3. Frequent black frames - breaks continuous matching
        # 4. Color adjustment
        
        video_filter = (
            # Speed up video by 5%
            "setpts=PTS/1.05,"
            # Scale down to 75% for larger border
            "scale=iw*0.75:ih*0.75,"
            # Add padding (colored border)
            "pad=iw/0.75:ih/0.75:(ow-iw)/2:(oh-ih)/2:color=#1a1a2e,"
            # Color adjustments
            "eq=brightness=0.04:saturation=1.08,"
            "hue=h=8,"
            # Insert black frame every 5 seconds (more frequent)
            "drawbox=enable='lt(mod(t,5),0.15)':c=black:t=fill"
        )
        
        # Audio filter to match video speed (pitch correction)
        audio_filter = "atempo=1.05"
        
        # Check if NVENC (GPU encoding) is available
        use_nvenc = check_nvenc_available()
        if use_nvenc:
            logger.debug("Using NVENC (GPU) for video encoding")
            video_codec_args = [
                "-c:v", "h264_nvenc",    # NVIDIA GPU encoder
                "-preset", "p4",          # Balanced speed/quality (p1=fastest, p7=best)
                "-cq", "18",              # Constant quality mode (similar to CRF)
                "-b:v", "0",              # Let CQ control quality
            ]
        else:
            logger.debug("NVENC not available, using CPU encoding")
            video_codec_args = [
                "-c:v", "libx264",        # CPU encoder
                "-preset", "fast",        # Fast encoding preset
                "-crf", "18",             # High quality
            ]
        
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",  # Overwrite output
            "-i", str(video_path),   # Input 0: original video
            "-i", str(audio_path),   # Input 1: new audio
            "-filter_complex", f"[0:v]{video_filter}[v];[1:a]{audio_filter}[a]",
            "-map", "[v]",           # Map filtered video
            "-map", "[a]",           # Map filtered audio
            *video_codec_args,       # Video encoding options
            "-c:a", "aac",           # Encode audio as AAC
            "-b:a", audio_bitrate,   # Audio bitrate
            "-shortest",             # Match shortest stream duration
            str(output_path)
        ]
    else:
        # Without watermark: copy video losslessly
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
            encoding='utf-8',
            errors='replace',  # Replace invalid characters instead of crashing
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


def get_video_duration(video_path: Path) -> float:
    """
    Get the duration of a video file in seconds.

    Args:
        video_path: Path to the video file

    Returns:
        Duration in seconds

    Raises:
        FFmpegError: If duration cannot be determined
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise FFmpegError(
                "Failed to get video duration",
                returncode=result.returncode,
                stderr=result.stderr
            )

        data = json.loads(result.stdout)
        if data.get("format") and "duration" in data["format"]:
            return float(data["format"]["duration"])
        else:
            raise FFmpegError("Duration not found in ffprobe output")

    except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        raise FFmpegError(f"Failed to parse video duration: {e}")
    except FileNotFoundError:
        raise PrerequisiteError("FFmpeg not found in system PATH")


def split_video(
    video_path: Path,
    output_dir: Path,
    chunk_duration: int = 900  # 15 minutes in seconds
) -> List[Path]:
    """
    Split a video file into chunks of specified duration.

    Args:
        video_path: Path to input video file
        output_dir: Directory to save chunks
        chunk_duration: Duration of each chunk in seconds (default: 900 = 15 minutes)

    Returns:
        List of paths to the video chunks

    Raises:
        FFmpegError: If splitting fails
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get total duration to calculate number of chunks
    total_duration = get_video_duration(video_path)
    num_chunks = int(total_duration / chunk_duration) + 1

    logger.info(f"Splitting video into {num_chunks} chunks of {chunk_duration}s each...")

    chunk_paths = []

    for i in range(num_chunks):
        start_time = i * chunk_duration
        chunk_path = output_dir / f"{video_path.stem}_chunk_{i:03d}.mp4"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",  # Overwrite output
            "-ss", str(start_time),  # Start time
            "-i", str(video_path),
            "-t", str(chunk_duration),  # Duration
            "-c", "copy",  # Copy without re-encoding (fast)
            "-avoid_negative_ts", "make_zero",  # Fix timestamp issues
            str(chunk_path)
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes per chunk
            )

            if result.returncode != 0:
                raise FFmpegError(
                    f"Failed to create chunk {i}",
                    returncode=result.returncode,
                    stderr=result.stderr
                )

            chunk_paths.append(chunk_path)
            logger.debug(f"Created chunk {i+1}/{num_chunks}: {chunk_path.name}")

        except subprocess.TimeoutExpired:
            raise FFmpegError(f"Chunk {i} creation timed out")

    logger.info(f"Successfully split video into {len(chunk_paths)} chunks")
    return chunk_paths


def concatenate_videos(
    video_paths: List[Path],
    output_path: Path
) -> Path:
    """
    Concatenate multiple video files into a single file.

    Args:
        video_paths: List of video file paths to concatenate (in order)
        output_path: Path for the output concatenated video

    Returns:
        Path to the concatenated video

    Raises:
        FFmpegError: If concatenation fails
    """
    if not video_paths:
        raise FFmpegError("No video files to concatenate")

    logger.info(f"Concatenating {len(video_paths)} video chunks...")

    # Create a temporary file list for FFmpeg concat demuxer
    concat_list_path = output_path.parent / f"{output_path.stem}_concat_list.txt"

    try:
        # Write the file list in FFmpeg concat format
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            for video_path in video_paths:
                # Use absolute paths and escape special characters
                abs_path = str(video_path.resolve()).replace('\\', '/')
                f.write(f"file '{abs_path}'\n")

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",  # Overwrite output
            "-f", "concat",  # Use concat demuxer
            "-safe", "0",  # Allow absolute paths
            "-i", str(concat_list_path),
            "-c", "copy",  # Copy without re-encoding (fast)
            str(output_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800  # 30 minutes timeout
        )

        if result.returncode != 0:
            raise FFmpegError(
                "Video concatenation failed",
                returncode=result.returncode,
                stderr=result.stderr
            )

        logger.info(f"Successfully concatenated videos to {output_path}")
        return output_path

    except subprocess.TimeoutExpired:
        raise FFmpegError("Video concatenation timed out")
    except FileNotFoundError:
        raise PrerequisiteError("FFmpeg not found in system PATH")
    finally:
        # Clean up the temporary concat list file
        if concat_list_path.exists():
            concat_list_path.unlink()


def remove_segments(
    video_path: Path,
    output_path: Path,
    remove_ranges: List[Tuple[float, float]],
    temp_dir: Optional[Path] = None
) -> Path:
    """
    Remove specific time ranges from a video by extracting and concatenating the segments to keep.

    Args:
        video_path: Path to input video file
        output_path: Path for output video
        remove_ranges: List of (start_time, end_time) tuples in seconds to remove
        temp_dir: Optional directory for temporary segment files

    Returns:
        Path to the output video with segments removed

    Raises:
        FFmpegError: If segment removal fails
    """
    if not remove_ranges:
        logger.warning("No segments to remove, copying file")
        import shutil
        shutil.copy2(video_path, output_path)
        return output_path

    # Sort remove_ranges by start time
    remove_ranges = sorted(remove_ranges, key=lambda x: x[0])

    # Get video duration
    total_duration = get_video_duration(video_path)

    # Calculate the segments to KEEP
    keep_ranges = []
    current_time = 0.0

    for start, end in remove_ranges:
        if current_time < start:
            keep_ranges.append((current_time, start))
        current_time = max(current_time, end)

    # Add final segment if there's time remaining
    if current_time < total_duration:
        keep_ranges.append((current_time, total_duration))

    if not keep_ranges:
        raise FFmpegError("All segments would be removed, no output possible")

    logger.info(f"Removing {len(remove_ranges)} segments, keeping {len(keep_ranges)} segments")

    # Use temp_dir or create one in the same directory as output
    if temp_dir is None:
        temp_dir = output_path.parent / "temp_segments"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Extract each segment to keep
        segment_paths = []
        for i, (start, end) in enumerate(keep_ranges):
            segment_path = temp_dir / f"segment_{i:03d}.mp4"
            duration = end - start

            logger.debug(f"Extracting segment {i+1}/{len(keep_ranges)}: {start:.2f}s - {end:.2f}s (duration: {duration:.2f}s)")

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss", str(start),
                "-i", str(video_path),
                "-t", str(duration),
                "-c", "copy",  # Copy without re-encoding (fast)
                "-avoid_negative_ts", "make_zero",
                str(segment_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600
            )

            if result.returncode != 0:
                raise FFmpegError(
                    f"Failed to extract segment {i}",
                    returncode=result.returncode,
                    stderr=result.stderr
                )

            segment_paths.append(segment_path)

        # Concatenate all kept segments
        logger.info("Concatenating kept segments...")
        concatenate_videos(segment_paths, output_path)

        # Calculate total removed duration
        removed_duration = sum(end - start for start, end in remove_ranges)
        final_duration = total_duration - removed_duration
        logger.info(f"Removed {removed_duration:.1f}s of content. Final duration: {final_duration:.1f}s (was {total_duration:.1f}s)")

        return output_path

    except subprocess.TimeoutExpired:
        raise FFmpegError("Segment extraction timed out")
    finally:
        # Clean up temporary segments
        if temp_dir.exists():
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.debug(f"Failed to clean up temp directory: {e}")
